// SPDX-License-Identifier: MIT
package cs2

import (
	"encoding/binary"
	"encoding/hex"
	"net"
	"testing"
)

func TestRelayWireAndDestinationGuards(t *testing.T) {
	for _, host := range []string{"47.88.30.183", "47.88.30.225", "127.0.0.1", "192.168.0.2", "0.0.0.0", "224.0.0.1"} {
		if relayAllowed(net.ParseIP(host)) {
			t.Fatal("prohibited destination accepted")
		}
	}
	if !relayAllowed(net.ParseIP("110.43.39.29")) {
		t.Fatal("public provisioned address rejected")
	}
	id, err := relayUID("ABCDEFG-123456-HIJKL")
	if err != nil || len(id) != 20 || id[7] != 0 || binary.BigEndian.Uint32(id[8:]) != 123456 || string(id[12:17]) != "HIJKL" {
		t.Fatal("UID layout")
	}
	if _, err = relayUID("ABCDEFGH-1-HIJKL"); err == nil {
		t.Fatal("oversize prefix")
	}
	address := &net.UDPAddr{IP: net.ParseIP("110.43.39.29"), Port: 25000}
	encoded := relayAddressBytes(address)
	if got := relayAddress(encoded); got == nil || got.String() != address.String() {
		t.Fatal("address layout")
	}
	encoded[1] = 0
	if relayAddress(encoded) != nil {
		t.Fatal("invalid address family accepted")
	}
	table, _ := hex.DecodeString("4959433db5bf6da347534f6165e371e9677f02030badb3892b2f35c16b8b959711e5a70deff1050783fb9d3bc5c713171d1f2529d3df")
	encode := func(plain string) string {
		b := make([]byte, len(plain)*2)
		state := byte(57)
		for i := range plain {
			v := plain[i] ^ state ^ table[i%len(table)]
			b[2*i] = 'A' + (v >> 4)
			b[2*i+1] = 'A' + (v & 15)
			state ^= plain[i]
		}
		return string(b)
	}
	servers, err := relayServers(encode("110.43.39.29,42.157.165.140,"))
	if err != nil || len(servers) != 2 || servers[0].Port != 32100 {
		t.Fatal("server decoding")
	}
	for _, s := range []string{"", "AAA", "ZZ", encode("127.0.0.1,"), encode("47.88.30.183,")} {
		if _, err := relayServers(s); err == nil {
			t.Fatal("invalid server configuration accepted")
		}
	}
	request := make([]byte, 88)
	request[0] = 0xa1
	request[1] = 1
	copy(request[4:], []byte{1, 2, 3, 4})
	copy(request[40:], relayAddressBytes(address))
	reply := relaySetup(request, &net.UDPAddr{IP: net.ParseIP("192.168.0.2"), Port: 12345})
	if len(reply) != 88 || reply[1] != 2 || request[1] != 1 || hex.EncodeToString(reply[56:64]) != "feffffff04030201" || hex.EncodeToString(reply[80:88]) != "020061a86e2b271d" {
		t.Fatal("MISS setup reply layout")
	}
	if relaySetup(request[:87], address) != nil {
		t.Fatal("short setup accepted")
	}
}
