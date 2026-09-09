param(
    [Parameter(Mandatory = $true)]
    [string]$BlenderPath
)

$ErrorActionPreference = 'Stop'
$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$BootstrapPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'blender_lifecycle_server.py')).Path
$DriverPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'run_lifecycle_validation.py')).Path
$ShutdownPath = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'validate_lifecycle_shutdown.py')).Path
$PythonPath = (Resolve-Path -LiteralPath (Join-Path $RepoRoot '.venv\Scripts\python.exe')).Path
$ResolvedBlenderPath = (Resolve-Path -LiteralPath $BlenderPath).Path

$PreviousAuthToken = [Environment]::GetEnvironmentVariable('BLENDER_MCP_AUTH_TOKEN', 'Process')
$PreviousPort = [Environment]::GetEnvironmentVariable('BLENDER_MCP_LIVE_TEST_PORT', 'Process')
$TokenBytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Fill($TokenBytes)
$env:BLENDER_MCP_AUTH_TOKEN = [Convert]::ToBase64String($TokenBytes).TrimEnd('=').Replace('+', '-').Replace('/', '_')

$PortProbe = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Loopback, 0)
$PortProbe.Start()
$env:BLENDER_MCP_LIVE_TEST_PORT = [string](($PortProbe.LocalEndpoint).Port)
$PortProbe.Stop()

$LogBase = Join-Path ([System.IO.Path]::GetTempPath()) ("blender_mcp_live_" + [Guid]::NewGuid().ToString('N'))
$StdOutPath = $LogBase + '.out.log'
$StdErrPath = $LogBase + '.err.log'
$TestProcess = $null
$ExitCode = 1

try {
    $QuotedBootstrapPath = '"' + $BootstrapPath + '"'
    $TestProcess = Start-Process `
        -FilePath $ResolvedBlenderPath `
        -ArgumentList @('--factory-startup', '--python-exit-code', '1', '--python', $QuotedBootstrapPath) `
        -WindowStyle Hidden `
        -PassThru `
        -RedirectStandardOutput $StdOutPath `
        -RedirectStandardError $StdErrPath

    $Deadline = [DateTime]::UtcNow.AddSeconds(25)
    $Ready = $false
    while ([DateTime]::UtcNow -lt $Deadline) {
        if ($TestProcess.HasExited) {
            break
        }
        $Client = [System.Net.Sockets.TcpClient]::new()
        try {
            $Connect = $Client.BeginConnect(
                '127.0.0.1',
                [int]$env:BLENDER_MCP_LIVE_TEST_PORT,
                $null,
                $null
            )
            if ($Connect.AsyncWaitHandle.WaitOne(150) -and $Client.Connected) {
                $Client.EndConnect($Connect)
                $Ready = $true
                break
            }
        }
        catch {
        }
        finally {
            $Client.Dispose()
        }
        Start-Sleep -Milliseconds 100
    }

    if (-not $Ready) {
        Write-Output '[BlenderMCP:LiveValidation] ERROR disposable server did not become ready'
        if (Test-Path -LiteralPath $StdOutPath) {
            Get-Content -LiteralPath $StdOutPath -Tail 80
        }
        if (Test-Path -LiteralPath $StdErrPath) {
            Get-Content -LiteralPath $StdErrPath -Tail 80
        }
        $ExitCode = 2
    }
    else {
        & $PythonPath $DriverPath
        $ExitCode = $LASTEXITCODE
        if ($ExitCode -ne 0) {
            Write-Output '[BlenderMCP:LiveValidation] Blender stdout tail follows'
            if (Test-Path -LiteralPath $StdOutPath) {
                Get-Content -LiteralPath $StdOutPath -Tail 120
            }
            Write-Output '[BlenderMCP:LiveValidation] Blender stderr tail follows'
            if (Test-Path -LiteralPath $StdErrPath) {
                Get-Content -LiteralPath $StdErrPath -Tail 120
            }
        }
    }
}
finally {
    if ($null -ne $TestProcess -and -not $TestProcess.HasExited) {
        $LiveProcess = Get-Process -Id $TestProcess.Id -ErrorAction SilentlyContinue
        if ($null -ne $LiveProcess -and $LiveProcess.Path -eq $ResolvedBlenderPath) {
            Stop-Process -Id $TestProcess.Id -Force
            $TestProcess.WaitForExit(5000) | Out-Null
        }
    }
    if ($null -eq $PreviousAuthToken) {
        Remove-Item Env:BLENDER_MCP_AUTH_TOKEN -ErrorAction SilentlyContinue
    }
    else {
        $env:BLENDER_MCP_AUTH_TOKEN = $PreviousAuthToken
    }
    if ($null -eq $PreviousPort) {
        Remove-Item Env:BLENDER_MCP_LIVE_TEST_PORT -ErrorAction SilentlyContinue
    }
    else {
        $env:BLENDER_MCP_LIVE_TEST_PORT = $PreviousPort
    }
}

if ($ExitCode -eq 0) {
    & $ResolvedBlenderPath --background --factory-startup --python-exit-code 1 --python $ShutdownPath
    $ExitCode = $LASTEXITCODE
}

exit $ExitCode
