// SPDX-License-Identifier: MIT
// CS2 relay transport, implemented from the observed PPPP/MISS wire format.
// Discovery destinations come exclusively from authenticated camera provisioning.
package cs2

import (
	"encoding/binary"
	"encoding/hex"
	"errors"
	"net"
	"strconv"
	"strings"
	"time"
)

func relayAllowed(ip net.IP) bool {
	return ip.To4() != nil && ip.IsGlobalUnicast() && !ip.IsPrivate() &&
		ip.String() != "47.88.30.183" && ip.String() != "47.88.30.225"
}

func relayServers(encoded string) ([]*net.UDPAddr, error) {
	encoded = strings.Split(encoded, ":")[0]
	if len(encoded) == 0 || len(encoded)%2 != 0 || len(encoded) > 2048 {
		return nil, errors.New("cs2 relay: invalid server configuration")
	}
	table, _ := hex.DecodeString("4959433db5bf6da347534f6165e371e9677f02030badb3892b2f35c16b8b959711e5a70deff1050783fb9d3bc5c713171d1f2529d3df")
	decoded := make([]byte, len(encoded)/2)
	state := byte(57)
	for i := range decoded {
		hi, lo := encoded[2*i], encoded[2*i+1]
		if hi < 'A' || hi > 'P' || lo < 'A' || lo > 'P' {
			return nil, errors.New("cs2 relay: invalid server encoding")
		}
		decoded[i] = state ^ table[i%len(table)] ^ ((hi-'A')<<4 | (lo - 'A'))
		state ^= decoded[i]
	}
	var out []*net.UDPAddr
	for _, host := range strings.Split(strings.TrimRight(string(decoded), ",\x00"), ",") {
		ip := net.ParseIP(host)
		if !relayAllowed(ip) {
			return nil, errors.New("cs2 relay: prohibited discovery destination")
		}
		out = append(out, &net.UDPAddr{IP: ip, Port: 32100})
	}
	return out, nil
}

func relayUID(text string) ([]byte, error) {
	p := strings.Split(text, "-")
	if len(p) != 3 || len(p[0]) == 0 || len(p[0]) > 7 || len(p[2]) == 0 || len(p[2]) > 5 {
		return nil, errors.New("cs2 relay: invalid identifier")
	}
	serial, err := strconv.ParseUint(p[1], 10, 32)
	if err != nil {
		return nil, errors.New("cs2 relay: invalid serial")
	}
	b := make([]byte, 20)
	copy(b, p[0])
	binary.BigEndian.PutUint32(b[8:], uint32(serial))
	copy(b[12:], p[2])
	return b, nil
}

func relayFrame(kind byte, body []byte) []byte {
	b := make([]byte, 4+len(body))
	b[0] = magic
	b[1] = kind
	binary.BigEndian.PutUint16(b[2:], uint16(len(body)))
	copy(b[4:], body)
	return b
}

func relayAddress(b []byte) *net.UDPAddr {
	if len(b) < 16 || b[0] != 0 || b[1] != 2 {
		return nil
	}
	ip := net.IPv4(b[7], b[6], b[5], b[4])
	port := int(binary.LittleEndian.Uint16(b[2:]))
	if !relayAllowed(ip) || port == 0 {
		return nil
	}
	return &net.UDPAddr{IP: ip, Port: port}
}

func relayAddressBytes(addr *net.UDPAddr) []byte {
	b := make([]byte, 16)
	b[1] = 2
	binary.LittleEndian.PutUint16(b[2:], uint16(addr.Port))
	ip := addr.IP.To4()
	for i := 0; i < 4; i++ {
		b[4+i] = ip[3-i]
	}
	return b
}

// relaySocket filters the source endpoint and consumes Xiaomi's linking exchange
// before passing reliable DRW messages to the existing CS2 channel implementation.
type relaySocket struct {
	*net.UDPConn
	remote, local *net.UDPAddr
}

func (s *relaySocket) RemoteAddr() net.Addr        { return s.remote }
func (s *relaySocket) Write(b []byte) (int, error) { return s.WriteToUDP(b, s.remote) }
func (s *relaySocket) Close() error                { _, _ = s.Write(relayFrame(0xf0, nil)); return s.UDPConn.Close() }
func (s *relaySocket) Read(b []byte) (int, error) {
	for {
		_ = s.SetReadDeadline(time.Now().Add(20 * time.Second))
		n, from, err := s.ReadFromUDP(b)
		if err != nil {
			return 0, err
		}
		if !from.IP.Equal(s.remote.IP) || from.Port != s.remote.Port || n < 4 || b[0] != magic || int(binary.BigEndian.Uint16(b[2:4])) != n-4 {
			continue
		}
		if b[1] == 0x28 {
			if reply := relaySetup(b[4:n], s.local); reply != nil {
				_, _ = s.Write(relayFrame(0x28, reply))
			}
			continue
		}
		if b[1] == 0x84 {
			continue
		}
		if b[1] == 0xf0 {
			return 0, errors.New("cs2 relay: peer closed session")
		}
		if b[1] == msgDrw && (n < 8 || b[4] != magicDrw) {
			continue
		}
		return n, nil
	}
}

func relaySetup(request []byte, local *net.UDPAddr) []byte {
	if len(request) != 88 || request[0] != 0xa1 || request[1] != 1 {
		return nil
	}
	reply := append([]byte(nil), request...)
	reply[1] = 2
	copy(reply[56:60], []byte{0xfe, 0xff, 0xff, 0xff})
	for i := 0; i < 4; i++ {
		reply[60+i] = request[7-i]
	}
	copy(reply[64:80], relayAddressBytes(local))
	reply[80] = 2
	reply[81] = 0
	reply[82] = request[43]
	reply[83] = request[42]
	for i := 0; i < 4; i++ {
		reply[84+i] = request[47-i]
	}
	return reply
}

// DialRelay uses registration tokens returned by the provisioned discovery
// servers, opens the allocated NAT mapping, then waits for the camera to join.
func DialRelay(uid, init string) (*Conn, error) {
	servers, err := relayServers(init)
	if err != nil {
		return nil, err
	}
	id, err := relayUID(uid)
	if err != nil {
		return nil, err
	}
	udp, err := net.ListenUDP("udp4", nil)
	if err != nil {
		return nil, err
	}
	success := false
	defer func() {
		if !success {
			_ = udp.Close()
		}
	}()
	deadline := time.Now().Add(50 * time.Second)
	allowed := map[string]bool{}
	for _, a := range servers {
		allowed[a.String()] = true
	}
	send := func(a *net.UDPAddr, kind byte, body []byte) {
		if relayAllowed(a.IP) {
			_, _ = udp.WriteToUDP(relayFrame(kind, body), a)
		}
	}
	receive := func(until time.Time) (byte, []byte, *net.UDPAddr) {
		buf := make([]byte, 2048)
		if until.After(deadline) {
			until = deadline
		}
		for time.Now().Before(until) {
			_ = udp.SetReadDeadline(until)
			n, from, e := udp.ReadFromUDP(buf)
			if e != nil {
				return 0, nil, nil
			}
			if !allowed[from.String()] || n < 4 || buf[0] != magic || int(binary.BigEndian.Uint16(buf[2:])) != n-4 {
				continue
			}
			return buf[1], append([]byte(nil), buf[4:n]...), from
		}
		return 0, nil, nil
	}
	registration := func(token []byte) []byte { b := make([]byte, 28); copy(b, token); copy(b[4:], id); return b }
	for time.Now().Before(deadline) {
		for _, a := range servers {
			send(a, 0x67, id)
		}
		type candidate struct {
			addr   *net.UDPAddr
			tokens [][]byte
		}
		candidates := map[string]*candidate{}
		until := time.Now().Add(2500 * time.Millisecond)
		for {
			kind, p, from := receive(until)
			if from == nil {
				break
			}
			// Only authenticated-config servers may introduce new destinations.
			isServer := false
			for _, a := range servers {
				if from.String() == a.String() {
					isServer = true
					break
				}
			}
			if kind != 0x82 || !isServer || len(p) < 20 {
				continue
			}
			a := relayAddress(p)
			if a == nil {
				continue
			}
			k := a.String()
			if candidates[k] == nil {
				candidates[k] = &candidate{addr: a}
			}
			token := append([]byte(nil), p[16:20]...)
			duplicate := false
			for _, t := range candidates[k].tokens {
				if string(t) == string(token) {
					duplicate = true
				}
			}
			if !duplicate {
				candidates[k].tokens = append(candidates[k].tokens, token)
			}
			allowed[k] = true
			allowed[(&net.UDPAddr{IP: a.IP, Port: 25000}).String()] = true
		}
		var chosen *net.UDPAddr
		for attempt := 0; attempt < 4 && chosen == nil && time.Now().Before(deadline); attempt++ {
			for _, c := range candidates {
				for _, t := range c.tokens {
					for i := 0; i < 4; i++ {
						send(c.addr, 0x83, registration(t))
					}
				}
				send(c.addr, 0x70, nil)
			}
			until = time.Now().Add(2500 * time.Millisecond)
			for {
				kind, _, from := receive(until)
				if from == nil {
					break
				}
				if kind == 0x71 {
					chosen = &net.UDPAddr{IP: from.IP, Port: 25000}
					break
				}
			}
		}
		if chosen == nil {
			continue
		}
		var token []byte
		var relay *net.UDPAddr
		for attempt := 0; attempt < 4 && relay == nil && time.Now().Before(deadline); attempt++ {
			send(chosen, 0x72, nil)
			until = time.Now().Add(2 * time.Second)
			for {
				kind, p, from := receive(until)
				if from == nil {
					break
				}
				if kind == 0x73 && from.IP.Equal(chosen.IP) && len(p) >= 6 {
					port := int(binary.BigEndian.Uint16(p[4:]))
					if port == 0 {
						continue
					}
					token = append([]byte(nil), p[:4]...)
					relay = &net.UDPAddr{IP: chosen.IP, Port: port}
					allowed[relay.String()] = true
					break
				}
			}
		}
		if relay == nil {
			continue
		}
		req := append(append(append([]byte(nil), id...), relayAddressBytes(relay)...), token...)
		ready := false
		for attempt := 0; attempt < 6 && !ready && time.Now().Before(deadline); attempt++ {
			for _, a := range servers {
				send(a, 0x80, req)
			}
			for i := 0; i < 24; i++ {
				send(relay, 0x83, registration(token))
			}
			until = time.Now().Add(2 * time.Second)
			for {
				kind, _, from := receive(until)
				if from == nil {
					break
				}
				if kind == 0x84 && from.String() == relay.String() {
					ready = true
					break
				}
			}
		}
		if !ready {
			continue
		}
		// Connect a separate socket only to consult the route, without transmitting.
		route, e := net.DialUDP("udp4", nil, relay)
		if e != nil {
			return nil, errors.New("cs2 relay: local route unavailable")
		}
		local := &net.UDPAddr{IP: route.LocalAddr().(*net.UDPAddr).IP, Port: udp.LocalAddr().(*net.UDPAddr).Port}
		_ = route.Close()
		_ = udp.SetDeadline(time.Time{})
		c := &Conn{Conn: &relaySocket{UDPConn: udp, remote: relay, local: local}, channels: [4]*dataChannel{newDataChannel(64, 32), nil, newDataChannel(1000, 256), nil}}
		success = true
		go c.worker()
		return c, nil
	}
	return nil, errors.New("cs2 relay: discovery or camera join timed out")
}
