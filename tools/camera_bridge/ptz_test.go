// SPDX-License-Identifier: MIT
package miss

import (
	"bytes"
	"encoding/binary"
	"testing"
	"time"
)

type ptzTestConn struct {
	Conn
	write func()
}

func (c *ptzTestConn) WriteCommand(cmd uint32, data []byte) error { c.write(); return nil }

func TestMotorWaitIgnoresOtherResponses(t *testing.T) {
	for _, matching := range []bool{true, false} {
		client := &Client{key: make([]byte, 32), replies: make(chan []byte, 8)}
		expected := append(binary.BigEndian.AppendUint32(nil, cmdMotorRes), []byte("{\"ret\":0}\x00")...)
		client.Conn = &ptzTestConn{write: func() {
			client.replies <- append(binary.BigEndian.AppendUint32(nil, cmdStreamCtrlRes), []byte(`{"ret":0}`)...)
			client.replies <- []byte(`{"ret":0}`) // Unframed data must not confirm a motor request.
			if matching {
				client.replies <- expected
			}
		}}
		actual, err := client.SendCommand(cmdMotorReq, []byte(`{"operation":2}`), 20*time.Millisecond)
		if err != nil {
			t.Fatal(err)
		}
		if matching && !bytes.Equal(actual, expected) {
			t.Fatal("motor response was replaced by an unrelated response")
		}
		if !matching && actual != nil {
			t.Fatal("unrelated response treated as motor acknowledgement")
		}
	}
}
