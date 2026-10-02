$winget = Get-Command winget -ErrorAction SilentlyContinue
if ($winget) { Write-Host "winget available: $($winget.Source)" } else { Write-Host "NO winget" }

$choco = Get-Command choco -ErrorAction SilentlyContinue
if ($choco) { Write-Host "choco available" } else { Write-Host "NO choco" }

$scoop = Get-Command scoop -ErrorAction SilentlyContinue
if ($scoop) { Write-Host "scoop available" } else { Write-Host "NO scoop" }