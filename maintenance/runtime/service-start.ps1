function Start-OwnedService {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string]$Name,
        [Parameter(Mandatory)][string]$Executable,
        [Parameter(Mandatory)][string]$Arguments,
        [Parameter(Mandatory)][string]$WorkingDirectory,
        [Parameter(Mandatory)][int]$Port,
        [Parameter(Mandatory)][string]$EntryMarker,
        [Parameter(Mandatory)][string]$PidFile,
        [Parameter(Mandatory)][string]$LogPrefix,
        [int]$StartupSeconds = 60
    )
    $mutex = [Threading.Mutex]::new($false, "Local\Ada-Service-$Name")
    $locked = $false
    try {
        try { $locked = $mutex.WaitOne(60000) }
        catch [Threading.AbandonedMutexException] { $locked = $true }
        if (-not $locked) { throw "$Name startup is already in progress." }
        $expectedExecutable = [IO.Path]::GetFullPath($Executable)
        $expectedMarker = [Regex]::Escape($EntryMarker)
        $isOwned = {
            param([int]$CandidateId)
            # Windows virtualenv launchers own a child interpreter. Validate
            # the full ancestry instead of trusting the PID file or the port.
            for ($depth = 0; $depth -lt 4 -and $CandidateId -gt 0; $depth++) {
                $candidate = Get-CimInstance Win32_Process -Filter "ProcessId=$CandidateId"
                if (-not $candidate) { return $false }
                if ($candidate.ExecutablePath -eq $expectedExecutable) {
                    return ($candidate.CommandLine -match ('(?:^|\s|")' + $expectedMarker + '(?:"|\s|$)'))
                }
                $CandidateId = [int]$candidate.ParentProcessId
            }
            return $false
        }
        $readListener = {
            $listeners = @(Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue)
            foreach ($listener in $listeners) {
                if (-not (& $isOwned ([int]$listener.OwningProcess))) {
                    throw "Port $Port is occupied by a different process; $Name was not started."
                }
            }
            if ($listeners.Count) { return [int]$listeners[0].OwningProcess }
            return 0
        }
        $listeningPid = & $readListener
        if ($listeningPid) {
            $listeningPid | Set-Content -LiteralPath $PidFile
            Write-Output "$Name is already listening on port $Port."
            return
        }
        $process = $null
        if (Test-Path -LiteralPath $PidFile) {
            $pendingId = 0
            if ([int]::TryParse((Get-Content -LiteralPath $PidFile -Raw).Trim(), [ref]$pendingId) -and
                (& $isOwned $pendingId)) {
                $process = Get-Process -Id $pendingId -ErrorAction SilentlyContinue
            }
        }
        if (-not $process) {
            $process = Start-Process -FilePath $expectedExecutable -ArgumentList $Arguments -WorkingDirectory $WorkingDirectory -WindowStyle Hidden -PassThru -RedirectStandardOutput "$LogPrefix.stdout.log" -RedirectStandardError "$LogPrefix.stderr.log"
            $process.Id | Set-Content -LiteralPath $PidFile
        }
        $deadline = [DateTime]::UtcNow.AddSeconds($StartupSeconds)
        do {
            $process.Refresh()
            if ($process.HasExited) { throw "$Name exited during startup. See $LogPrefix.stderr.log." }
            $listeningPid = & $readListener
            if ($listeningPid) {
                $listeningPid | Set-Content -LiteralPath $PidFile
                Write-Output "$Name is listening on port $Port (PID $listeningPid)."
                return
            }
            Start-Sleep -Milliseconds 250
        } while ([DateTime]::UtcNow -lt $deadline)
        # A timeout is not success. Do not kill an unverified process or retry
        # automatically while an earlier attempt may still be starting.
        throw "$Name did not listen on port $Port within $StartupSeconds seconds. Inspect its logs before retrying."
    }
    finally {
        if ($locked) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}
