# Build VoxKey-Setup.exe in the cloud (GitHub Actions)

This lets Microsoft's own Windows servers compile the installer for you.
No Python, no PATH problems, nothing to install on your PC. You end up
downloading a finished `VoxKey-Setup.exe`.

You need a free GitHub account. The whole thing takes about 5 minutes of
clicking, then ~10 minutes of waiting while it builds.

---

## Step 1 — Create a GitHub account (skip if you have one)

Go to https://github.com and sign up. Free tier is all you need.

## Step 2 — Create a new repository

1. Click the **+** in the top-right → **New repository**.
2. Name it anything, e.g. `voxkey`.
3. You can leave it **Private** — Actions still works on private repos.
4. Click **Create repository**.

## Step 3 — Upload the project

On your new empty repo page:

1. Click **uploading an existing file** (the link in the "Quick setup" box),
   or go to **Add file → Upload files**.
2. Unzip the `VoxKey.zip` I gave you on your computer first.
3. Drag **everything inside** the unzipped folder into the browser upload area
   — including the hidden `.github` folder. If Windows hides it, see the note
   below.
4. Scroll down, click **Commit changes**.

> **Important — the `.github` folder must be included.** That folder holds the
> build instructions. On Windows, hidden folders starting with a dot can be
> awkward to drag. Easiest fix: in File Explorer, enable **View → Show → Hidden
> items**, then drag the `.github` folder in with the rest. If it still won't
> upload via drag, use **Add file → Create new file**, type
> `.github/workflows/build.yml` as the name (GitHub creates the folders as you
> type the slashes), and paste in the contents of that file from the zip.

## Step 4 — Watch it build

1. Click the **Actions** tab at the top of your repo.
2. You'll see a run called **Build Windows Installer** already going (it starts
   automatically on upload). If it asks you to enable workflows, click the green
   **I understand my workflows, enable them** button.
3. Click into the run. You'll see the steps tick green one by one. It takes
   roughly 8–12 minutes (installing dependencies + Inno Setup is the slow part).

If a step goes red, click it to see the error and send me a screenshot — I'll
fix it.

## Step 5 — Download your installer

1. When the run finishes (green tick), scroll to the bottom of the run page to
   the **Artifacts** section.
2. Download **VoxKey-Setup** — it's a zip containing `VoxKey-Setup.exe`.
3. Unzip it, double-click `VoxKey-Setup.exe`, and you get the classic Windows
   Setup wizard. It installs VoxKey and launches it when done.

There's also a **VoxKey-app** artifact — that's the standalone app folder if you
ever want to run `VoxKey.exe` directly without installing.

---

## Re-building later

Any time you change the code and upload again (or click **Run workflow** on the
Actions tab), it rebuilds and produces a fresh installer automatically.

## Why this works when local builds failed

The build runs on `windows-latest` — a clean, correctly-configured Windows
machine with Python and Chocolatey already set up properly. None of the
local PATH / "Python was not found" problems can happen there.
