package main

import (
	"bufio"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
)

const (
	defaultRepoURL = "https://github.com/mcarmody/karakos-package.git"
	defaultDir     = "karakos"
	version        = "1.0.0"
)

// repoURL returns the canonical install source, overridable for forks via
// KARAKOS_REPO_URL or KARAKOS_REPO=user/repo.
func repoURL() string {
	if u := os.Getenv("KARAKOS_REPO_URL"); u != "" {
		return u
	}
	if r := os.Getenv("KARAKOS_REPO"); r != "" {
		return "https://github.com/" + r + ".git"
	}
	return defaultRepoURL
}

// ANSI colors
const (
	green  = "\033[0;32m"
	yellow = "\033[1;33m"
	red    = "\033[0;31m"
	cyan   = "\033[0;36m"
	reset  = "\033[0m"
)

func main() {
	printBanner()

	installDir := resolveInstallDir()

	steps := []struct {
		name string
		fn   func() error
	}{
		{"Checking prerequisites", checkPrereqs},
		{"Installing missing dependencies", installDeps},
		{"Cloning repository", func() error { return cloneRepo(installDir) }},
		{"Running setup wizard", func() error { return runSetup(installDir) }},
	}

	for _, step := range steps {
		stepLog(step.name)
		if err := step.fn(); err != nil {
			errLog(fmt.Sprintf("%s: %s", step.name, err))
			if step.name == "Installing missing dependencies" {
				errLog("Please install the missing dependencies manually and run this installer again.")
			}
			os.Exit(1)
		}
	}

	fmt.Println()
	printSuccess(installDir)
}

func printBanner() {
	fmt.Println()
	fmt.Printf("%s╔══════════════════════════════════╗%s\n", cyan, reset)
	fmt.Printf("%s║       Karakos Installer v%s    ║%s\n", cyan, version, reset)
	fmt.Printf("%s╚══════════════════════════════════╝%s\n", cyan, reset)
	fmt.Println()
}

func printSuccess(dir string) {
	fmt.Printf("%s╔══════════════════════════════════════════════╗%s\n", green, reset)
	fmt.Printf("%s║  Installation complete!                       ║%s\n", green, reset)
	fmt.Printf("%s╚══════════════════════════════════════════════╝%s\n", green, reset)
	fmt.Println()
	fmt.Printf("  Install directory: %s\n", dir)
	fmt.Printf("  Logs: docker compose -f %s/config/docker-compose.yml logs -f\n", dir)
	fmt.Println()
}

func resolveInstallDir() string {
	home, _ := os.UserHomeDir()
	dir := filepath.Join(home, defaultDir)

	// Allow override via argument
	if len(os.Args) > 1 {
		dir = os.Args[1]
	}

	return dir
}

// --- Prerequisite Checks ---

type dep struct {
	name    string
	check   func() bool
	install func() error
	fatal   bool // if true, installer cannot continue without it
}

var missingDeps []dep

func checkPrereqs() error {
	deps := getDeps()
	missingDeps = nil

	for _, d := range deps {
		if d.check() {
			okLog(fmt.Sprintf("%s found", d.name))
		} else {
			warnLog(fmt.Sprintf("%s not found", d.name))
			missingDeps = append(missingDeps, d)
		}
	}

	return nil
}

func installDeps() error {
	if len(missingDeps) == 0 {
		okLog("All dependencies present")
		return nil
	}

	for _, d := range missingDeps {
		stepLog(fmt.Sprintf("Installing %s...", d.name))
		if d.install == nil {
			if d.fatal {
				return fmt.Errorf("cannot auto-install %s on this platform — please install manually", d.name)
			}
			warnLog(fmt.Sprintf("Cannot auto-install %s — skipping", d.name))
			continue
		}
		if err := d.install(); err != nil {
			if d.fatal {
				return fmt.Errorf("failed to install %s: %w", d.name, err)
			}
			warnLog(fmt.Sprintf("Failed to install %s: %s", d.name, err))
		} else {
			okLog(fmt.Sprintf("%s installed", d.name))
		}
	}

	// Special case: Docker Desktop may need a restart
	if !commandExists("docker") {
		warnLog("Docker was just installed but may require a restart.")
		warnLog("Please restart your computer, launch Docker Desktop, then run this installer again.")
		os.Exit(0)
	}

	// Verify Docker is actually running
	if !dockerRunning() {
		errLog("Docker is installed but not running.")
		if runtime.GOOS == "darwin" {
			errLog("Please launch Docker Desktop from Applications and wait for it to start.")
		} else if runtime.GOOS == "windows" {
			errLog("Please launch Docker Desktop from the Start menu and wait for it to start.")
		} else {
			errLog("Try: sudo systemctl start docker")
		}
		os.Exit(1)
	}

	return nil
}

func getDeps() []dep {
	switch runtime.GOOS {
	case "windows":
		return windowsDeps()
	case "darwin":
		return macDeps()
	default:
		return linuxDeps()
	}
}

func windowsDeps() []dep {
	return []dep{
		{
			name:  "Git",
			check: func() bool { return commandExists("git") },
			install: func() error {
				return runCmd("winget", "install", "--id", "Git.Git", "-e",
					"--accept-source-agreements", "--accept-package-agreements")
			},
			fatal: true,
		},
		{
			name:  "Docker",
			check: func() bool { return commandExists("docker") && dockerRunning() },
			install: func() error {
				return runCmd("winget", "install", "--id", "Docker.DockerDesktop", "-e",
					"--accept-source-agreements", "--accept-package-agreements")
			},
			fatal: true,
		},
		{
			name:  "jq",
			check: func() bool { return commandExists("jq") },
			install: func() error {
				return runCmd("winget", "install", "--id", "jqlang.jq", "-e",
					"--accept-source-agreements", "--accept-package-agreements")
			},
			fatal: true,
		},
	}
}

func macDeps() []dep {
	hasBrew := commandExists("brew")
	return []dep{
		{
			name:  "Homebrew",
			check: func() bool { return hasBrew },
			install: func() error {
				return runShell(`/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"`)
			},
			fatal: true,
		},
		{
			name:  "Git",
			check: func() bool { return commandExists("git") },
			install: func() error {
				if hasBrew {
					return runCmd("brew", "install", "git")
				}
				return fmt.Errorf("install Homebrew first")
			},
			fatal: true,
		},
		{
			name:  "Docker",
			check: func() bool { return commandExists("docker") && dockerRunning() },
			install: func() error {
				if hasBrew {
					return runCmd("brew", "install", "--cask", "docker")
				}
				return fmt.Errorf("install Homebrew first, or download Docker Desktop from docker.com")
			},
			fatal: true,
		},
		{
			name:  "jq",
			check: func() bool { return commandExists("jq") },
			install: func() error {
				if hasBrew {
					return runCmd("brew", "install", "jq")
				}
				return fmt.Errorf("install Homebrew first")
			},
			fatal: true,
		},
	}
}

func linuxDeps() []dep {
	pm := detectPackageManager()
	return []dep{
		{
			name:  "Git",
			check: func() bool { return commandExists("git") },
			install: func() error {
				return installWithPM(pm, "git")
			},
			fatal: true,
		},
		{
			name:  "Docker",
			check: func() bool { return commandExists("docker") && dockerRunning() },
			install: func() error {
				return runShell("curl -fsSL https://get.docker.com | sh")
			},
			fatal: true,
		},
		{
			name:  "jq",
			check: func() bool { return commandExists("jq") },
			install: func() error {
				return installWithPM(pm, "jq")
			},
			fatal: true,
		},
		{
			name:  "curl",
			check: func() bool { return commandExists("curl") },
			install: func() error {
				return installWithPM(pm, "curl")
			},
			fatal: true,
		},
	}
}

// --- Repository ---

func cloneRepo(dir string) error {
	if _, err := os.Stat(dir); err == nil {
		// Directory exists
		gitDir := filepath.Join(dir, ".git")
		if _, err := os.Stat(gitDir); err == nil {
			stepLog("Repository already cloned, pulling latest...")
			return runCmdInDir(dir, "git", "pull", "origin", "main")
		}
		// Directory exists but isn't a git repo — ask before overwriting
		fmt.Printf("  Directory %s exists but isn't a Karakos installation.\n", dir)
		fmt.Print("  Remove and re-clone? (y/N): ")
		if !promptYes() {
			return fmt.Errorf("installation cancelled")
		}
		os.RemoveAll(dir)
	}

	return runCmd("git", "clone", repoURL(), dir)
}

// --- Setup Wizard ---

func runSetup(dir string) error {
	setupScript := filepath.Join(dir, "setup.sh")

	// Find bash
	bashPath := findBash()
	if bashPath == "" {
		return fmt.Errorf("bash not found — required for setup wizard")
	}

	cmd := exec.Command(bashPath, setupScript)
	cmd.Dir = dir
	cmd.Stdin = os.Stdin
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr

	return cmd.Run()
}

// --- Start System ---

func startSystem(dir string) error {
	configDir := filepath.Join(dir, "config")
	return runCmdInDir(configDir, "docker", "compose", "up", "-d")
}

// --- Helpers ---

func commandExists(name string) bool {
	_, err := exec.LookPath(name)
	return err == nil
}

func dockerRunning() bool {
	cmd := exec.Command("docker", "info")
	cmd.Stdout = nil
	cmd.Stderr = nil
	return cmd.Run() == nil
}

func runCmd(name string, args ...string) error {
	cmd := exec.Command(name, args...)
	cmd.Stdin = os.Stdin
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	return cmd.Run()
}

func runCmdInDir(dir string, name string, args ...string) error {
	cmd := exec.Command(name, args...)
	cmd.Dir = dir
	cmd.Stdin = os.Stdin
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	return cmd.Run()
}

func runShell(command string) error {
	shell := "sh"
	flag := "-c"
	if runtime.GOOS == "windows" {
		shell = "cmd"
		flag = "/c"
	}
	cmd := exec.Command(shell, flag, command)
	cmd.Stdin = os.Stdin
	cmd.Stdout = os.Stdout
	cmd.Stderr = os.Stderr
	return cmd.Run()
}

func findBash() string {
	if runtime.GOOS == "windows" {
		// Windows: check known Git Bash locations FIRST to avoid WSL shim
		candidates := []string{
			filepath.Join(os.Getenv("ProgramFiles"), "Git", "bin", "bash.exe"),
			filepath.Join(os.Getenv("ProgramFiles(x86)"), "Git", "bin", "bash.exe"),
			filepath.Join(os.Getenv("LOCALAPPDATA"), "Programs", "Git", "bin", "bash.exe"),
			`C:\Program Files\Git\bin\bash.exe`,
		}
		for _, c := range candidates {
			if _, err := os.Stat(c); err == nil {
				return c
			}
		}

		// Fall back to PATH but reject WSL shim (System32 or WindowsApps)
		if path, err := exec.LookPath("bash"); err == nil {
			lower := strings.ToLower(path)
			if !strings.Contains(lower, "system32") && !strings.Contains(lower, "windowsapps") {
				return path
			}
		}

		return ""
	}

	// Non-Windows: just use PATH
	if path, err := exec.LookPath("bash"); err == nil {
		return path
	}

	return ""
}

func detectPackageManager() string {
	managers := []string{"apt-get", "dnf", "yum", "pacman", "zypper", "apk"}
	for _, m := range managers {
		if commandExists(m) {
			return m
		}
	}
	return ""
}

func installWithPM(pm string, pkg string) error {
	switch pm {
	case "apt-get":
		if err := runCmd("sudo", "apt-get", "update", "-qq"); err != nil {
			return err
		}
		return runCmd("sudo", "apt-get", "install", "-y", "-qq", pkg)
	case "dnf":
		return runCmd("sudo", "dnf", "install", "-y", pkg)
	case "yum":
		return runCmd("sudo", "yum", "install", "-y", pkg)
	case "pacman":
		return runCmd("sudo", "pacman", "-S", "--noconfirm", pkg)
	case "zypper":
		return runCmd("sudo", "zypper", "install", "-y", pkg)
	case "apk":
		return runCmd("sudo", "apk", "add", pkg)
	default:
		return fmt.Errorf("no supported package manager found — install %s manually", pkg)
	}
}

func promptYes() bool {
	scanner := bufio.NewScanner(os.Stdin)
	if scanner.Scan() {
		answer := strings.TrimSpace(strings.ToLower(scanner.Text()))
		return answer == "y" || answer == "yes"
	}
	return false
}

// --- Logging ---

func stepLog(msg string) {
	fmt.Printf("%s==>%s %s\n", green, reset, msg)
}

func okLog(msg string) {
	fmt.Printf("    %s✓%s %s\n", green, reset, msg)
}

func warnLog(msg string) {
	fmt.Printf("    %s!%s %s\n", yellow, reset, msg)
}

func errLog(msg string) {
	fmt.Printf("%sError:%s %s\n", red, reset, msg)
}
