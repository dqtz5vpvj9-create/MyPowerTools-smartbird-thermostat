# SmartBird Thermostat for MyPowerTools

This repository owns the `smartbird-thermostat` source snapshot and its
buildable MyPowerTools adapter.

## Repository layout

- `original-source/` contains the original SmartBird thermostat and Energy
  Server Python sources.
- `current-integration/` contains the suite adapter source, package manifest,
  Web/Shell integration snapshot, UI metadata, and related tests.
- `build.ps1` builds the adapter against the public projects in a MyPowerTools
  superproject checkout.
- `tool-release.json` defines the adapter, package template, output contract,
  and required suite project references.
- `artifacts/package/` is the generated package staging directory and is ignored
  by Git.

## Build

From a MyPowerTools submodule checkout, the script discovers the superproject:

```powershell
pwsh.exe -NoLogo -NoProfile -NonInteractive -File .\build.ps1
```

From an independent checkout, pass the suite path explicitly:

```powershell
pwsh.exe -NoLogo -NoProfile -NonInteractive -File .\build.ps1 `
  -MyPowerToolsRepoRoot C:\path\to\MyPowerTools `
  -Configuration Release
```

The adapter project requires the `MyPowerToolsRepoRoot` MSBuild property and
references `MyPowerTools.Abstractions` and `MyPowerTools.Protocol` from that
checkout. Successful builds stage the manifest, UI resources, adapter DLL, and
PDB under `artifacts/package`.

Package integrity metadata must be refreshed by the suite signing step after the
adapter binary is injected. The Python runtime remains represented under
`original-source/`; runtime packaging is a separate release step.

## Repository URL

The current development submodule URL is
`file:///C:/Users/lixinrui/repo/MyPowerTools.ToolRepos/smartbird-thermostat`.
Replace the entry in the superproject `.gitmodules` file after publishing this
repository to its permanent remote.
