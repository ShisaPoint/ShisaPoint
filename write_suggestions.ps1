# write_suggestions.ps1 - write ShisaPoint suggestions safely (UTF-8 without BOM, atomic).
# Usage (any one):
#   & "$env:USERPROFILE\ShisaPoint\write_suggestions.ps1" -Json $json      # best: no encoding loss
#   powershell -File write_suggestions.ps1 -Path C:\temp\s.json
#   $json | powershell -File write_suggestions.ps1     (stdin; non-ASCII may be lost on PS 5.1)
# Validates the JSON first, writes snapshots\suggestions.tmp next to the target, then
# Move-Item -Force onto snapshots\suggestions.json (paths relative to this script's folder
# unless -Out is given). Skips the write when the content is unchanged (prints UNCHANGED).
param(
  [string]$Json,
  [string]$Path,
  [string]$Out,
  [switch]$Force
)
$ErrorActionPreference = 'Stop'
$here = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $Out) { $Out = Join-Path (Join-Path $here 'snapshots') 'suggestions.json' }
if (-not $Json -and $Path) { $Json = [IO.File]::ReadAllText($Path) }
if (-not $Json -and $MyInvocation.ExpectingInput) { $Json = ($input | Out-String) }
if (-not $Json -and [Console]::IsInputRedirected) { $Json = [Console]::In.ReadToEnd() }
if (-not $Json -or -not $Json.Trim()) { Write-Output 'ERROR: no JSON given (-Json, -Path or stdin)'; exit 2 }
$Json = $Json.Trim().TrimStart([char]0xFEFF)
try { $null = $Json | ConvertFrom-Json } catch { Write-Output "ERROR: invalid JSON: $($_.Exception.Message)"; exit 3 }
$dir = Split-Path -Parent $Out
if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
$utf8 = New-Object Text.UTF8Encoding $false
if (-not $Force -and (Test-Path -LiteralPath $Out)) {
  $old = [IO.File]::ReadAllText($Out).TrimStart([char]0xFEFF)
  if ($old -eq $Json) { Write-Output "UNCHANGED $Out"; exit 0 }
}
$tmp = Join-Path $dir 'suggestions.tmp'
[IO.File]::WriteAllText($tmp, $Json, $utf8)
Move-Item -LiteralPath $tmp -Destination $Out -Force
Write-Output "WROTE $Out"
