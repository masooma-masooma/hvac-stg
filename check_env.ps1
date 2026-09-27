Write-Host ""
Write-Host "================== D7065E SYSTEM READINESS CHECK ==================" -ForegroundColor Cyan
Write-Host ""

$checks = @(
    @{ Name = "Python";         Cmd = "python --version";         Install = "Install from https://python.org" },
    @{ Name = "Go (Golang)";    Cmd = "go version";               Install = "Install from https://go.dev (or add C:\Program Files\Go\bin to PATH)" },
    @{ Name = "Docker";         Cmd = "docker --version";         Install = "Install Docker Desktop from https://docker.com" },
    @{ Name = "Docker Compose"; Cmd = "docker compose version";   Install = "Included automatically with Docker Desktop" },
    @{ Name = "Git";            Cmd = "git --version";            Install = "Install from https://git-scm.com" },
    @{ Name = "WSL 2";          Cmd = "wsl --status";             Install = "Run in admin terminal: wsl --install" },
    @{ Name = "D2 Diagrams";    Cmd = "d2 --version";             Install = "Run: go install oss.terrastruct.com/d2@latest" },
    @{ Name = "VS Code";        Cmd = "code --version";           Install = "Install from https://code.visualstudio.com" }
)

foreach ($c in $checks) {
    try {
        $exe = ($c.Cmd -split " ")[0]
        $found = Get-Command $exe -ErrorAction SilentlyContinue
        if ($found) {
            $ver = (Invoke-Expression $c.Cmd 2>&1 | Select-Object -First 1).ToString().Trim()
            Write-Host (" [OK]      " + $c.Name.PadRight(16) + " : " + $ver) -ForegroundColor Green
        } else {
            Write-Host (" [MISSING] " + $c.Name.PadRight(16) + " -> Action: " + $c.Install) -ForegroundColor Red
        }
    } catch {
        Write-Host (" [MISSING] " + $c.Name.PadRight(16) + " -> Action: " + $c.Install) -ForegroundColor Red
    }
}

Write-Host ""
Write-Host "===================================================================" -ForegroundColor Cyan
Write-Host ""

