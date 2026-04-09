package dashboard

import (
	"embed"
	"io/fs"
)

//go:embed web/*
var webContent embed.FS

var webFS, _ = fs.Sub(webContent, "web")
