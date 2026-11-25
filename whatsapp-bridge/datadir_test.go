package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestEnterDataDir(t *testing.T) {
	orig, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = os.Chdir(orig) })

	t.Setenv("DATA_DIR", "")
	if err := enterDataDir(); err != nil {
		t.Fatalf("unset DATA_DIR: %v", err)
	}
	if wd, _ := os.Getwd(); wd != orig {
		t.Fatalf("unset DATA_DIR changed directory to %s", wd)
	}

	dir := filepath.Join(t.TempDir(), "wa")
	t.Setenv("DATA_DIR", dir)
	if err := enterDataDir(); err != nil {
		t.Fatalf("enterDataDir: %v", err)
	}
	wd, _ := os.Getwd()
	want, _ := filepath.EvalSymlinks(dir)
	if got, _ := filepath.EvalSymlinks(wd); got != want {
		t.Fatalf("working directory = %s, want %s", got, want)
	}
	info, err := os.Stat(dir)
	if err != nil {
		t.Fatal(err)
	}
	if info.Mode().Perm() != 0o700 {
		t.Fatalf("DATA_DIR mode = %v, want 0700", info.Mode().Perm())
	}
}
