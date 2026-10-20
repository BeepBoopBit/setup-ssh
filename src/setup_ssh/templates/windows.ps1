$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$p = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__PAYLOAD__')) | ConvertFrom-Json
$utf8 = New-Object Text.UTF8Encoding($false)
$sshDir = Join-Path $env:USERPROFILE '.ssh'
$groupDir = Join-Path $sshDir ('setup-ssh/' + $p.group)
$keyPath = Join-Path $groupDir 'id_ed25519'
$begin = '# BEGIN setup-ssh ' + $p.group
$end = '# END setup-ssh ' + $p.group

function Assert-RegularPath([string]$Path) {
    if (Test-Path -LiteralPath $Path) {
        $item = Get-Item -Force -LiteralPath $Path
        if ($item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Refusing a directory or reparse point: $Path"
        }
    }
}

function Protect-UserPath([string]$Path) {
    $owner = [Security.Principal.WindowsIdentity]::GetCurrent().User
    $system = New-Object Security.Principal.SecurityIdentifier('S-1-5-18')
    $item = Get-Item -Force -LiteralPath $Path
    if ($item.PSIsContainer) {
        $acl = New-Object Security.AccessControl.DirectorySecurity
    } else {
        $acl = New-Object Security.AccessControl.FileSecurity
    }
    $acl.SetAccessRuleProtection($true, $false)
    $acl.SetOwner($owner)
    foreach ($sid in @($owner, $system)) {
        if ($item.PSIsContainer) {
            $rule = [Security.AccessControl.FileSystemAccessRule]::new(
                $sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
        } else {
            $rule = [Security.AccessControl.FileSystemAccessRule]::new($sid, 'FullControl', 'Allow')
        }
        $acl.AddAccessRule($rule)
    }
    Set-Acl -LiteralPath $Path -AclObject $acl
}

function Write-Owned([string]$Path, [string]$Content) {
    Assert-RegularPath $Path
    $value = $Content.TrimEnd("`r", "`n") + "`n"
    if ((Test-Path -LiteralPath $Path) -and [IO.File]::ReadAllText($Path) -ceq $value) { return }
    $temp = $Path + '.tmp.' + [Guid]::NewGuid().ToString('N')
    try {
        [IO.File]::WriteAllText($temp, $value, $utf8)
        Protect-UserPath $temp
        if (Test-Path -LiteralPath $Path) {
            [IO.File]::Replace($temp, $Path, [NullString]::Value)
        } else {
            [IO.File]::Move($temp, $Path)
        }
    } finally {
        if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp -Force }
    }
}

function Write-Managed([string]$Path, [string]$Body, [bool]$Prepend) {
    Assert-RegularPath $Path
    $original = ''
    if (Test-Path -LiteralPath $Path) { $original = [IO.File]::ReadAllText($Path) }
    $lines = $original.Replace("`r`n", "`n").Split([char]10)
    $kept = New-Object 'Collections.Generic.List[string]'
    $inside = $false
    $seen = $false
    foreach ($line in $lines) {
        if ($line -ceq $begin) {
            if ($inside -or $seen) { throw "Malformed setup-ssh block in $Path" }
            $inside = $true; $seen = $true; continue
        }
        if ($line -ceq $end) {
            if (-not $inside) { throw "Malformed setup-ssh block in $Path" }
            $inside = $false; continue
        }
        if (-not $inside) { $kept.Add($line) }
    }
    if ($inside) { throw "Malformed setup-ssh block in $Path; restore its backup." }
    $remainder = [string]::Join("`n", $kept).TrimEnd("`n")
    $block = ''
    if ($Body) { $block = $begin + "`n" + $Body.TrimEnd("`r", "`n") + "`n" + $end }
    $pieces = @()
    if ($Prepend -and $block) { $pieces += $block }
    if ($remainder) { $pieces += $remainder }
    if (-not $Prepend -and $block) { $pieces += $block }
    $value = [string]::Join("`n", $pieces) + "`n"
    if ($original.Replace("`r`n", "`n") -cne $value) {
        if (Test-Path -LiteralPath $Path) {
            Copy-Item -LiteralPath $Path -Destination ($Path + '.setup-ssh.bak') -Force
        }
        Write-Owned $Path $value
    }
}

function Authorized-Path {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    # Membership also detects UAC-filtered administrator tokens in a local shell.
    if ($identity.Groups.Value -contains 'S-1-5-32-544') {
        return Join-Path $env:ProgramData 'ssh/administrators_authorized_keys'
    }
    return Join-Path $sshDir 'authorized_keys'
}

try {
    if ($p.action -eq 'check') {
        $configPath = Join-Path $groupDir 'config'
        if (-not (Test-Path -LiteralPath $configPath)) { throw 'No managed configuration. Run sync.' }
        & ssh.exe '-F' $configPath '-o' 'BatchMode=yes' '-o' 'PasswordAuthentication=no' `
            '-o' 'KbdInteractiveAuthentication=no' '-o' 'PreferredAuthentications=publickey' `
            '-o' 'IdentityAgent=none' '-o' 'ConnectionAttempts=1' `
            '-o' ('ConnectTimeout=' + $p.timeout) $p.destination 'echo setup-ssh-ok'
        exit $LASTEXITCODE
    }
    New-Item -ItemType Directory -Path $groupDir -Force | Out-Null
    Protect-UserPath $groupDir
    if ($p.action -eq 'prepare') {
        Get-Command ssh.exe, ssh-keygen.exe -ErrorAction Stop | Out-Null
        Assert-RegularPath $keyPath
        Assert-RegularPath ($keyPath + '.pub')
        if (-not (Test-Path -LiteralPath $keyPath)) {
            if (Test-Path -LiteralPath ($keyPath + '.pub')) {
                throw 'Managed private key is missing; restore it. To replace it, remove this device and delete its orphaned .pub file before re-enrolling.'
            }
            # ProcessStartInfo preserves the empty -N argument on Windows PowerShell 5.1.
            $start = New-Object Diagnostics.ProcessStartInfo
            $start.FileName = (Get-Command ssh-keygen.exe).Source
            $start.UseShellExecute = $false
            $start.CreateNoWindow = $true
            $start.Arguments = '-q -t ed25519 -N "" -f "' + $keyPath + '" -C "setup-ssh:' + $p.group + ':' + $p.device + '"'
            $process = [Diagnostics.Process]::Start($start)
            $process.WaitForExit()
            if ($process.ExitCode -ne 0) { throw 'ssh-keygen failed.' }
        }
        Protect-UserPath $keyPath
        $start = New-Object Diagnostics.ProcessStartInfo
        $start.FileName = (Get-Command ssh-keygen.exe).Source
        $start.UseShellExecute = $false
        $start.RedirectStandardOutput = $true
        $start.RedirectStandardError = $true
        $start.Arguments = '-y -P "" -f "' + $keyPath + '"'
        $process = [Diagnostics.Process]::Start($start)
        $public = $process.StandardOutput.ReadToEnd().Trim()
        $keyError = $process.StandardError.ReadToEnd()
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) { throw ('Cannot read managed key: ' + $keyError) }
        Write-Owned ($keyPath + '.pub') ($public + ' setup-ssh:' + $p.group + ':' + $p.device)
        Write-Output ('SETUP_SSH_KEY=' + $public)
    } elseif ($p.action -eq 'apply' -or $p.action -eq 'remove') {
        $authorized = Authorized-Path
        if ($p.action -eq 'apply') {
            if (-not (Test-Path -LiteralPath $keyPath)) { throw 'Managed private key missing. Run sync.' }
            Write-Owned (Join-Path $groupDir 'config') $p.config
            Write-Owned (Join-Path $groupDir 'known_hosts') $p.known_hosts
            Write-Managed $authorized $p.authorized_keys $false
            Write-Managed (Join-Path $sshDir 'config') ('Include "~/.ssh/setup-ssh/' + $p.group + '/config"') $true
        } else {
            Write-Managed $authorized '' $false
            Write-Managed (Join-Path $sshDir 'config') '' $true
        }
        if ($authorized -eq (Join-Path $env:ProgramData 'ssh/administrators_authorized_keys')) {
            # These SIDs work on localized Windows installations too.
            $acl = New-Object Security.AccessControl.FileSecurity
            $acl.SetAccessRuleProtection($true, $false)
            foreach ($sidValue in @('S-1-5-18', 'S-1-5-32-544')) {
                $sid = New-Object Security.Principal.SecurityIdentifier($sidValue)
                $rule = New-Object Security.AccessControl.FileSystemAccessRule($sid, 'FullControl', 'Allow')
                $acl.AddAccessRule($rule)
            }
            Set-Acl -LiteralPath $authorized -AclObject $acl
            if (Test-Path -LiteralPath ($authorized + '.setup-ssh.bak')) {
                Set-Acl -LiteralPath ($authorized + '.setup-ssh.bak') -AclObject $acl
            }
        } else {
            Protect-UserPath $authorized
        }
    } else {
        throw ('Unknown action: ' + $p.action)
    }
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    exit 1
}
