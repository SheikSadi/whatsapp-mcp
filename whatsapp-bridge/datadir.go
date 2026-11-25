package main

import (
	"os"
	"strings"
)

// enterDataDir makes DATA_DIR (when set) the working directory, so the
// bridge's relative store/ paths (whatsapp.db, messages.db, media, the bridge
// token) all live under $DATA_DIR/store. Unset keeps the upstream behaviour
// (store/ next to wherever the bridge was started).
func enterDataDir() error {
	dir := strings.TrimSpace(os.Getenv("DATA_DIR"))
	if dir == "" {
		return nil
	}
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return err
	}
	return os.Chdir(dir)
}
