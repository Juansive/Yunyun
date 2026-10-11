<h1 align="center">Yunyun 芸芸</h1>

<p align="center">A little AI companion that lives on your Windows desktop</p>

<p align="center">
  <a href="https://github.com/Juansive/Yunyun/releases/latest"><b>Download</b></a> ·
  <a href="#features"><b>Features</b></a> ·
  <a href="#getting-started"><b>Get started</b></a> ·
  <a href="#assistant-mode"><b>Assistant mode</b></a> ·
  <a href="#privacy"><b>Privacy</b></a> ·
  <a href="README.zh-CN.md"><b>简体中文</b></a> ·
  <a href="README.ja.md"><b>日本語</b></a>
</p>

Yunyun keeps you company while you type, watches your inbox, and tells you in one sentence what each new email is about.
Turn on Assistant mode and she can also chat, look things up, edit code, look at your screen, open apps, and browse the web for you.

> The character art is still a placeholder — the real artwork is on its way.

## Features

- **Keeps you company**: she "types" along with you, dozes off when you stop, and moves her mouth when she talks. Drag her anywhere on the desktop.
- **Watches your mail**: when new mail arrives, a **red badge** appears on her shoulder — within 15 seconds for QQ Mail, within seconds for mailboxes that push (like Gmail); mailboxes without push (like 163 Mail) are checked every 2 minutes. Supports QQ Mail, 163 Mail and Gmail.
- **One-sentence reminders**: click the badge and Yunyun uses AI to turn each email into one sentence — e.g. "Prof. Wang says chapter 3 of your thesis needs more work; send it by Monday" — shown as a stack of cards.
- **Stays out of your way**: pauses when covered by other windows, never pops up during full-screen videos, games or slides, and has quiet hours (she goes to sleep at night).
- **Nothing gets lost**: reminders you've seen are kept for 7 days.
- **Never touches your mailbox**: emails Yunyun reads stay "unread" in your mailbox.
- **AI assistant** (since 0.6, Full edition only): right-click → "✦ Assistant mode", using your own DeepSeek / Claude API key (pay as you go). See [Assistant mode](#assistant-mode).
- **Watch mode**: when you use Claude Code yourself, Yunyun lets you know when it finishes or needs your approval.
- UI in English, 中文 and 日本語 (right-click → Settings… → General → Language).

## Download and install

Requires Windows 10/11 (64-bit). **No Python needed.**

1. Open **Releases** on the right of this page (or [click here](https://github.com/Juansive/Yunyun/releases/latest)) and pick one of the two files under Assets:

   | File | Size | What's inside |
   |---|---|---|
   | **`Yunyun-<version>-windows-lite.zip`** (Lite) | ~35 MB, ~80 MB unzipped | Desktop pet, new-mail reminders, Watch mode. **Pick this if you only want mail reminders** |
   | **`Yunyun-<version>-windows.zip`** (Full) | ~200 MB, ~470 MB unzipped | Everything above, plus Assistant mode (the extra size is the Claude Code and browser driver it needs) |

   Unzip it anywhere you like (e.g. `D:\Yunyun`). Start with Lite; if you want Assistant mode later, download Full and replace the folder — your settings are kept.
   > ⚠️ Don't click the green **Code → Download ZIP** button: that's the source code and needs Python to run.
2. Double-click **`芸芸.exe`** in the folder. Keep the whole folder together — don't copy the exe out on its own (it needs the files in `_internal` next to it).
3. On first launch Windows may say "Windows protected your PC" (Yunyun has no paid code-signing certificate): click **More info → Run anyway**.
4. Once Yunyun appears, **right-click her → Settings… → General** to create a desktop shortcut or start her with Windows.

**Upgrading**: when a new version is out, Yunyun tells you, and **⬆ Download version …** appears at the top of the right-click menu (you can also use Settings → About → "Check for Updates").
After downloading, right-click Yunyun → quickly double-click "Quit", then replace the old folder with the new one. Your mail, AI settings and history live in `%APPDATA%\Yunyun` and are kept.
If you moved the folder, recreate the desktop shortcut and re-tick "Start with Windows" in Settings… → General (they point to the old location).

## Run from source

Requires Windows 10/11 and Python 3.11+. Open a terminal in the project folder:

```
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m yunyun.main
```

Once Yunyun appears, **right-click her → Settings… → General → Desktop shortcut "Create"**. From then on, double-click "芸芸 Yunyun" on the desktop — no terminal needed.

## Getting started

1. **Add a mailbox**: on first launch Yunyun opens the "Mail" page of the settings window (you can change it later in right-click → Settings… → Mail).
   Type your email address and the provider is picked for you. QQ Mail needs an **authorization code**, not your QQ password: QQ Mail (web) → Settings → Account & Security → enable "IMAP/SMTP service" → generate a code.
   163 Mail also uses an authorization code; Gmail uses an "app password" (turn on 2-Step Verification first). The instructions on the settings page have clickable links.
2. **Set up AI** (optional, recommended): after you add a mailbox, the settings window switches to the "AI" page (also at right-click → Settings… → AI). The default is Zhipu's free model:
   sign up at [open.bigmodel.cn](https://open.bigmodel.cn), create a key under "API Keys", and paste it in.
   Without AI, Yunyun still works — she just tells you who sent what subject.
   **Want your mail to never leave your PC?** Choose "Local model (Ollama)". Install Ollama from [ollama.com/download](https://ollama.com/download) first —
   no sign-up, no key. **Yunyun starts Ollama when processing mail and shuts it down 20 seconds later**, so it uses no memory otherwise; while processing it temporarily uses about 1–2 GB,
   and it's roughly 1 second slower than keeping Ollama running. A dedicated GPU makes it much faster.
   By default Ollama starts with Windows and stays in the background (~160 MB). If it's only for Yunyun, you can disable its startup in Task Manager → "Startup apps".
   **Want to use your own model?** Choose "Custom" and fill in the API URL (any OpenAI-compatible service, usually ending in `/v1`) and the model name; the key is optional.
   Online examples: DeepSeek (`https://api.deepseek.com/v1`); on your own PC: LM Studio (`http://localhost:1234/v1`).
   With a local or LAN address, your mail never goes onto the internet.

## Everyday use

| You want to… | Do this |
|---|---|
| Read new mail | Click the **red badge** on Yunyun's shoulder |
| Skip this batch | **Drag the badge away** and let go, or right-click Yunyun → Mark All as Seen |
| Hide the badge for now | **Right-click** the badge; **right-click Yunyun** to bring it back |
| Browse the cards | **Scroll** or use ↑↓; **click a card** to see the subject, sender and attachments. With two or more mailboxes, a small tag in the card's bottom-right corner shows which mailbox received it |
| Close the cards | Click elsewhere, press Esc, or click the badge again |
| See the last batch again | Right-click Yunyun → **Recent Mail** |
| Look through older reminders | Right-click → Settings… → **History** (last 7 days) |
| Not be disturbed at night | Right-click → Settings… → General → **Do not disturb** (23:00–08:00 by default). Yunyun sleeps and new mail is just counted quietly; she wakes up when you type |
| Yunyun is hidden behind windows | Double-click her icon in the system tray |
| Keep Yunyun on top | Right-click → Settings… → General → Always on Top |
| Make Yunyun bigger / smaller | Right-click → Settings… → General → Size (50%–200%, updates live; bubbles and cards stay the same size) |
| Start with Windows | Right-click → Settings… → General → Start with Windows |
| Move her | Just drag Yunyun; she'll be there next time too |
| Quit | Right-click → **quickly double-click** "Quit" (prevents accidental clicks) |

The badge only counts unread mail that arrives **after Yunyun starts watching your mailbox** — old unread mail isn't counted.

## Assistant mode

> Available since 0.6, in the Full edition only (see [Download and install](#download-and-install)). The Lite edition has no such menu item.

Right-click Yunyun → the first item **✦ Assistant mode**. An input box with a soft blue glow unfolds at her feet; ask her anything, and she'll search the web when needed.
Her "brain" is Claude (Anthropic) by default, or the cheaper **DeepSeek**. **Both need your own API key and are pay-as-you-go** (Claude's API is billed separately from a Claude subscription).

1. Get a key:
   - **DeepSeek** (cheap; easy to sign up and pay from China): [platform.deepseek.com](https://platform.deepseek.com/api_keys) → sign up with a phone number → top up (Alipay / WeChat Pay) → API keys → create.
   - **Claude**: [platform.claude.com](https://platform.claude.com) → sign up → Billing (needs a card that can pay in USD) → API Keys → Create Key.
2. Right-click Yunyun → Settings… → **Assistant**: choose the provider, paste the key, and click "Test and Save" (it only checks the key and model — **free**). The default is the cheapest model, Claude Haiku 4.5.
   DeepSeek can't search the web (it can read URLs you give it); the settings page shows its account balance.

| You want to… | Do this |
|---|---|
| Ask something | Type in the box and press Enter (Shift+Enter for a new line). Yunyun says the start of her answer in a bubble above her head as she thinks |
| Make her stop | Click ■ on the right of the input box while she's answering; if the box is closed, right-click Yunyun → "■ Stop the assistant". Running commands are stopped too |
| Read the full answer | Each question and answer becomes a card next to Yunyun; scroll through them and **click a card** to open the full text beside it. Code blocks have a copy button |
| Start a new topic | Hover over the cards and click "+ New chat" in the top-right corner, or press Ctrl+N in the input box. The card stack only holds the current chat; earlier ones are in Chat history |
| Continue yesterday's chat | Right-click Yunyun → "Chat history…" (or "History" on the card stack): chats are listed by date; click one to see all of it, then "Continue" to pick up where you left off (it switches back to that chat's working folder). Kept for 30 days |
| Have her work on code | Click the **folder button** on the left of the input box and choose a working folder (e.g. your project). She can read and search files there; she asks before changing files or running commands |
| Approve / deny | A confirmation card slides out next to Yunyun: the full command, or the file changes (red removed, green added). Choose "Allow once", allow it for this chat, or "Deny" (you can give a reason). Commands that delete files, install software or download from the internet are shown in red and always asked |
| See what she did | Open that answer's card and click "▸ Yunyun did N things": which files she read, which commands she ran, and whether each one was automatic, allowed or denied by you |
| Undo her file edits | Open that answer's card and click "↶ Undo these changes" (twice). Only edits she made with the file-editing tool can be undone; changes made by commands (e.g. files a command created or deleted) can't — use git. Not available after starting a new chat |
| Bring her up | First time: right-click → "✦ Assistant mode"; after that, just **left-click Yunyun** |
| Put her away | Press Esc or click elsewhere: only the input box folds away — the cards and any opened answer **stay** (handy for reading docs side by side). If you've typed something or attached images, clicking elsewhere won't close it (press Esc), nor will dragging files in from Explorer |
| Hide the cards | Right-click Yunyun → "Hide cards"; left-click Yunyun to bring them back (along with the answer you had open) |
| Move the cards | Drag an empty part of the card stack or the answer panel (it stays on screen); from then on the cards follow Yunyun at that offset. Right-click → "Put cards back next to Yunyun" to reset |
| Let her see your screen | Say "look at this error on my screen": she says "Let me take a look at your screen", captures the window you were just using (the whole screen if needed) and answers. Her own windows are never captured. No confirmation by default; tick "Ask me before taking a screenshot" in Settings → Assistant to be asked each time |
| Have her open an app | Say "open Calculator": she finds it in the Start menu by name and asks you first (the card shows the app's name). You can allow that one app for the rest of the chat. She can only open apps that are in the Start menu, and doesn't operate them |
| Have her use a browser | Say "take a look at xxx website": she opens her own Edge window (you can see it) and answers after reading. It's separate from your own browser; sites you log into there stay logged in next time; password and card-number fields are never shown to her. While the browser is open the background isn't released; it uses 0.4–1+ GB depending on the pages, so **close that window when you're done** |
| Have her act on a web page | Say "search for cats on Bilibili" or "fill in this form for me": she asks before every click, typing or dropdown choice (the card shows the site and the button / text), and you can allow everything on that site for this chat. Submitting, ordering, paying, sending, deleting and downloading are always asked and shown in red; searching doesn't count. She never fills in passwords, card numbers or verification codes — type those yourself in the window |
| Have her download a file | She always asks first; files go to your Downloads folder (name clashes become "name (1).zip", nothing is overwritten). Downloads you start yourself in her browser window go there too, and she tells you when they finish |
| Show her an image | Paste a copied image with Ctrl+V or drag image files into the box: thumbnails appear above it (× to remove) and are sent with your text (up to 5 at a time). Dragging in other files inserts their paths |
| Have her remember you | Say "remember …" and she will; she also notes useful things in her **Notebook** on her own (your preferences, project conventions, pitfalls), so she remembers them in later chats. Notes "about you" are used in every chat; project notes only in the same working folder. View, edit, delete or turn it off in Settings → "Notebook" |
| Talk to her | Settings → "Voice": set a push-to-talk key (e.g. Ctrl+Shift+Space or F9) and click "Download" for the speech model (about 240 MB, once). Then, in assistant mode, hold the key, speak, and let go to send — she answers out loud too. While you hold it she shows a listening look with a bouncing volume bar; if she's talking, she stops to listen. The key only works while assistant mode is open and not during quiet hours |
| Have her read replies aloud | By default she reads her reply when you asked by voice; set "Read replies aloud" to "Every reply" in Settings → "Voice" to hear typed questions answered too. Only the prose is read (code, tables and links are skipped; file paths become just the file name). 8 voices (Chinese / Japanese / English) with a preview button. Right-click → "Stop talking" stops her; a new question, Stop or the push-to-talk key does too |
| Exit | Right-click → Exit assistant mode. If the input box stays closed for 10 minutes, the background is released first (~300 MB) while the cards stay; asking again reconnects to the same chat automatically |

- **Cost**: with Haiku 4.5, each question is usually around 1 US cent (the first question of a chat costs a bit more). In the settings page you can change "Max per reply" (default $0.1) and
  "Max per chat" (default $0.5), and see how much Yunyun has spent today / this month / in total. Anthropic has no balance API, so check your balance on their website.
  With DeepSeek a question usually costs ¥0.01–0.03 (measured about $0.002–0.005); Yunyun calculates it with DeepSeek's prices (peak-hour rates, to err on the high side).
  Questions where she does a lot of work (reading many files, running commands, browsing) cost more.
- **Long chats**: once a chat goes over about 40k tokens, Yunyun summarizes the earlier part after answering and continues with just the summary (she stays sharp and it's cheaper,
  though small details may be lost). "Chat 23k/40k" at the bottom of the cards is the current length; change the length or turn it off in Settings → Assistant → "Tidy up long chats".
- **Memory use**: only while Assistant mode is open, about 360 MB more (Claude Code is ~300 MB of that); it's all given back when you exit.
- Without a working folder she can only chat and search the web. If the working folder has a `CLAUDE.md` (project instructions), she follows it; settings in `.claude\` are not loaded — what needs your approval is decided by Yunyun alone.
- She can open apps from the Start menu but can't operate inside them (clicking buttons, typing) yet — maybe in a later version.

## Watch mode: let Yunyun keep an eye on your Claude Code

When you use Claude Code yourself in a terminal, VS Code or the Claude desktop app, Yunyun can watch it for you: when it **finishes a turn, waits for your permission, or asks you to pick an option**,
Yunyun tells you ("Claude Code is done · project name", with its first sentence below). **She stays quiet if you're already looking at that window**; if Yunyun is covered by other windows,
a small bar pops up above the taskbar — click it to switch to Claude Code. No need to open Assistant mode, and it works in both editions.

- Turn it on: Settings → "Watch" → "Connect my Claude Code". This adds two hooks to `C:\Users\you\.claude\settings.json`
  (they run Yunyun's tiny script when Claude Code finishes or waits for you — it takes a few dozen milliseconds and does nothing if Yunyun isn't running). The file is backed up in the same folder first; your own hooks and other settings are left alone.
- Claude Code windows that are already open need to be restarted. Click "Send a test reminder" to see what it looks like.
- Don't want it any more? "Disconnect" removes only Yunyun's two hooks.

## Privacy

| Data | Where it's stored |
|---|---|
| Mail authorization codes, AI API key, assistant API keys | Windows Credential Manager (`yunyun-mail`, `yunyun-ai`, `yunyun-assistant`), protected by your Windows account |
| Assistant chat logs | `%APPDATA%\Yunyun\Yunyun\assistant\claude\` (Claude Code's own session logs, **including the full conversations and the screenshots Yunyun took**) |
| Assistant answer cards (chat history) | `%APPDATA%\Yunyun\Yunyun\assistant\cards.json` (**full questions and answers, plus what she did**); like the logs and undo backups above, **kept for 30 days**. Settings → Assistant → "Clear chat history" deletes them right away |
| File backups for undo | Same folder (Claude Code backs up a file before changing it, for undo; kept for 30 days) |
| Yunyun's browser | `%APPDATA%\Yunyun\Yunyun\assistant\browser\` (Edge profile: **sites you logged into, browsing history, cookies**; not cleaned automatically — exit Assistant mode and delete this folder to clear it) |
| Working folders (last 5) | `%APPDATA%\Yunyun\Yunyun\config.yaml` |
| Yunyun's Notebook | `%APPDATA%\Yunyun\Yunyun\assistant\memory.json` (what she noted; Settings → Notebook → "Clear all" deletes it) |
| Assistant spending | `%APPDATA%\Yunyun\Yunyun\assistant_spending.json` (dates and amounts only) |
| Email addresses, AI provider | `%APPDATA%\Yunyun\Yunyun\config.yaml` |
| Which emails were reminded | `%APPDATA%\Yunyun\Yunyun\mail_state.json` (message IDs only, no content) |
| Reminder history | `%APPDATA%\Yunyun\Yunyun\history.db` (the AI sentence, subject, sender, time, attachment names, receiving mailbox — no body; kept for 7 days) |
| Logs | `%APPDATA%\Yunyun\Yunyun\logs\` (no email content or keys; email addresses are masked) |
| Language, window position and other settings | Registry `HKEY_CURRENT_USER\Software\Yunyun` |
| Start with Windows | A "芸芸 Yunyun" shortcut in your Startup folder |

- **What's sent to the AI**: only **after you click the badge** are each email's sender, subject, attachment names and the beginning of the body (up to 1,500 characters) sent to the AI provider you chose. Emails you dismiss by dragging the badge away are never sent.
  With "Local model (Ollama)" this only goes to Ollama on your PC, never onto the internet; with "Custom" it goes to the address you entered (a local or LAN address also stays off the internet).
- **Emails you don't want the AI to see**: in the "Privacy" box at the bottom of Settings → AI, tick whole mailboxes or add sender addresses / `@domains` (e.g. `@icbc.com.cn` covers everything from that domain). Those emails only show the sender and subject (with 🔒) and nothing is sent to any AI — local models included.
- **New-version check**: at most once a day Yunyun asks GitHub (`api.github.com`) for the latest version number; the request only contains the app name and version, none of your information. Turn it off in Settings → General.
- **No sensitive info in reminders**: verification codes, pickup codes, amounts, card numbers, order numbers and links are removed automatically even if the AI writes them (reminders appear on your desktop and are saved in history).
- **Assistant mode**: your questions and the conversation go to the provider you chose (Claude / DeepSeek), and so do the web pages she searches and reads; if you chose a working folder,
  so do the files she reads, command output and the project's `CLAUDE.md`; at the start of each chat, the Notebook's index (one line per note) is sent too;
  when she looks at your screen, the screenshot is sent (Yunyun's own windows are never captured).
  When she reads a reply aloud, the text being read goes to Microsoft's free online voice service (if it can't be reached, the voice built into Windows is used, offline).
  What you say with the push-to-talk key is turned into text on your computer only — recordings are never uploaded or saved (the text is sent like a typed question).
  Nothing is sent unless you open Assistant mode.
- **Keyboard**: Yunyun only notices *whether* you're typing — **it never records which keys you press**.

## FAQ

**The badge never shows up?** Only new mail triggers it. Try sending yourself an email from another account: QQ Mail shows it within 15 seconds, Gmail within seconds, other mailboxes within 2 minutes (it may take a minute or two right after startup or a network drop). Click Yunyun and she'll tell you if there's no new mail.

**How do I report a problem?** Right-click → Settings… → About → Open Log Folder, and send `yunyun.log` to the developer.

**How do I remove all of Yunyun's data?** First untick "Start with Windows" in right-click → Settings… → General (and if you used Watch mode, Settings → Watch → "Disconnect"); delete Yunyun's folder (or the project folder), the desktop shortcut and `%APPDATA%\Yunyun`; in Control Panel → Credential Manager → Windows Credentials, delete the entries starting with `yunyun-mail`, `yunyun-ai` and `yunyun-assistant`; delete the registry key `HKEY_CURRENT_USER\Software\Yunyun`.

## Development

```
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest            # run the tests
.\.venv\Scripts\python -m yunyun.mail       # debug: read unread mail from the command line
.\.venv\Scripts\python tools\make_placeholders.py   # regenerate the placeholder art
.\.venv\Scripts\python tools\build_exe.py   # build the exe (or double-click tools\build_exe.bat)
```

It builds both editions: Full `dist\芸芸\芸芸.exe` and Lite `dist\lite\芸芸\芸芸.exe` (without the Assistant mode components; `build_exe.py lite` / `full` builds just one).
The zips for sharing go to `..\Yunyun\` next to the project folder: `Yunyun-<version>-windows.zip` and `Yunyun-<version>-windows-lite.zip` (only the latest is kept there; older ones move to `releases\`).
The build script runs the exe's self-check in an environment "without Python" (Full: 9 lines of `OK`, two of which really start Claude Code and a hidden Edge,
then it tries the exe as the "assistant subprocess"; Lite: 8 lines, the last confirming the assistant components are left out), and only zips if everything passes.
The zip includes [docs/使用说明.txt](docs/使用说明.txt) (the user guide, in Chinese, with the version filled in) and the [changelog](docs/更新日志.txt) (Chinese); developer notes are never included (the script checks).
Before sharing, bump the version in `yunyun/__init__.py`, then upload both zips to GitHub Releases.

- Developer notes (CLAUDE.md, design notes, plans, handoff) live only on the developer's own machine, not in this repository.
- Requirements for the character artwork: [assets/pet/README.md](assets/pet/README.md) (Chinese).
