# How to put this project on GitHub

## Option A: Using the GitHub website (no software needed)

1. Sign in at https://github.com (create a free account if you do not have one).
2. Click **+** (top right), then **New repository**.
3. Repository name: `network-latency-monitor`. Choose **Public**. Do **not** tick "Add a README" (this project already has one). Click **Create repository**.
4. On the new empty repository page, click **uploading an existing file**.
5. Unzip `network-latency-monitor.zip` on your computer. Open the unzipped `network-latency-monitor` folder, select **everything inside it** (including the `.github` folder and `.gitignore`; on Windows turn on *View > Show > Hidden items* so they are visible) and drag it into the browser.
6. Type a message such as `Initial version` and click **Commit changes**.
7. Open the **Actions** tab: the automatic tests run and should show a green tick.
8. Edit `README.md` on GitHub and replace `<your-username>` with your GitHub username.

## Option B: Using Git on your computer

```bash
cd network-latency-monitor
git init
git add .
git commit -m "Initial version"
git branch -M main
git remote add origin https://github.com/<your-username>/network-latency-monitor.git
git push -u origin main
```

## After uploading

Anyone can now install and run the program with:

```bash
git clone https://github.com/<your-username>/network-latency-monitor.git
cd network-latency-monitor
python latency_monitor.py
```

or install it as a command:

```bash
pip install git+https://github.com/<your-username>/network-latency-monitor.git
latency-monitor
```
