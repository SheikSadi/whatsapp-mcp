package main

import (
	"os"
	"strings"
)

// logLevel returns WHATSAPP_LOG_LEVEL (DEBUG, INFO, WARN, ERROR), defaulting
// to DEBUG as before. DEBUG logs raw WhatsApp stanzas, including pairing QR
// payloads and message contents, so long-running services should use INFO.
func logLevel() string {
	switch v := strings.ToUpper(strings.TrimSpace(os.Getenv("WHATSAPP_LOG_LEVEL"))); v {
	case "DEBUG", "INFO", "WARN", "ERROR":
		return v
	default:
		return "DEBUG"
	}
}

// stdoutIsTerminal reports whether stdout is an interactive terminal (as
// opposed to journald, a pipe or a file). Colours and secrets are for terminals only.
func stdoutIsTerminal() bool {
	info, err := os.Stdout.Stat()
	return err == nil && info.Mode()&os.ModeCharDevice != 0
}
