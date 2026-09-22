// golangci-lint, pinned, in a module file of its own.
module github.com/codesweep-ai/goproject/internal/golangci

go 1.27.0

tool github.com/golangci/golangci-lint/v2/cmd/golangci-lint

require (
	github.com/golangci/golangci-lint/v2 v2.13.1 // indirect
	golang.org/x/tools v0.49.0 // indirect
)
