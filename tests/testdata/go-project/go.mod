module github.com/codesweep-ai/goproject

go 1.27.0

tool (
	github.com/codesweep-ai/ledger/cmd/cs-ledger
	golang.org/x/tools/cmd/deadcode
)

require (
	github.com/codesweep-ai/ledger v0.0.0-20260918043804-bbe29a48e449 // indirect
	go.yaml.in/yaml/v3 v3.0.4
	golang.org/x/tools v0.49.0 // indirect
)
