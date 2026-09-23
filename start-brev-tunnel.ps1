param(
    [Parameter(Mandatory=$true)][string]$SshHost,
    [Parameter(Mandatory=$true)][string]$SshUser,
    [Parameter(Mandatory=$true)][int]$SshPort,
    [Parameter(Mandatory=$true)][string]$KeyPath,
    [Parameter(Mandatory=$true)][string]$KnownHostsPath
)
$ErrorActionPreference = 'Stop'
$keyFile = (Resolve-Path -LiteralPath $KeyPath).Path
$hostsFile = (Resolve-Path -LiteralPath $KnownHostsPath).Path
Write-Host 'Connecting to Brev. Keep this terminal open. Ctrl+C stops the tunnel.'
& ssh -N -L '127.0.0.1:18000:127.0.0.1:8000' -p $SshPort `
    -i $keyFile -o 'IdentitiesOnly=yes' -o 'BatchMode=yes' `
    -o 'KexAlgorithms=curve25519-sha256' -o 'HostKeyAlgorithms=ssh-ed25519' `
    -o "UserKnownHostsFile=$hostsFile" -o 'StrictHostKeyChecking=yes' `
    -o 'ExitOnForwardFailure=yes' -o 'ConnectTimeout=15' `
    -o 'ServerAliveInterval=30' -o 'ServerAliveCountMax=3' `
    "${SshUser}@${SshHost}"
exit $LASTEXITCODE
