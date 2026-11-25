package main

import "testing"

func TestLogLevel(t *testing.T) {
	cases := map[string]string{"": "DEBUG", "info": "INFO", " WARN ": "WARN", "error": "ERROR", "verbose": "DEBUG"}
	for in, want := range cases {
		t.Setenv("WHATSAPP_LOG_LEVEL", in)
		if got := logLevel(); got != want {
			t.Errorf("WHATSAPP_LOG_LEVEL=%q: got %s, want %s", in, got, want)
		}
	}
}

func TestStdoutIsTerminalUnderTest(t *testing.T) {
	// go test captures stdout through a pipe/file, never a terminal.
	if stdoutIsTerminal() {
		t.Skip("stdout is a terminal in this environment")
	}
}
