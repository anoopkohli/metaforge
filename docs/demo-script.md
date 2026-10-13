# Demo script (about 90 seconds)

A screen recording of the local UI, offline (no API key needed). Record with QuickTime
(File → New Screen Recording, select the browser window only). Browser window about 1280×860, zoom
100%, bookmarks bar hidden, notifications off.

## Setup (not recorded)

```bash
make pg-up   # optional: adds the Postgres row-level-security rows to the security lab
make ui      # http://127.0.0.1:8765
```

Restart `make ui` right before recording so the audit log starts clean. Company: **fjord · Fjord Taxi Co.**

## Shots

| Time | Do | Say (voice-over or caption) |
|---|---|---|
| 0:00–0:08 | Overview tab, slowly | "A multi-tenant app with seven AI features. I built it with AI coding tools and treated every AI as untrusted, including the one writing the code." |
| 0:08–0:25 | Sign in as **Amir Ito · Driver**. Assistant tab → ask "How many rides have I driven, and what is my average rating?" | "A driver asks the assistant. It runs real queries, but only over this driver's own rides: 21 of them. The scope comes from the sign-in, not from anything the model says." |
| 0:25–0:42 | Sign in as **Ben Okafor · Dispatcher**. Ask data → "What are the average salaries by department?" Show the denial and the rejected attempts. Then sign in as **Tara Ekwueme · Operations Manager** and ask again. | "Staff can't read salaries: not in the select, a filter, an average or a sort. A manager can. The model writes a small JSON query; the server checks every clause." |
| 0:42–1:05 | Incident tab → start a **queue backlog** with "plant a malicious instruction" ticked → choose **Fooled AI** and run it. Pause on the blocked step and on "ignored tenant_id". | "Now the AI reads a log line an attacker planted, and obeys it: scale to 5,000 workers, for another company. The fix is outside policy, so it's blocked; the tenant it tried to smuggle in is ignored. The safe proposal still waits for a human." |
| 1:05–1:18 | Audit log tab → **Verify the chain**. | "Every sign-in, question, denial and proposal is in a hash-chained audit log. Verify finds any edited or deleted entry." |
| 1:18–1:30 | Security lab → **Assistant · 11**. Show the green and red columns. | "53 attacks like these run on every push. Each one is blocked in the real code and succeeds against a deliberately broken copy, so I know the test can fail. Link to the repo and the log of every AI mistake below." |

## Tips

- Do one dry run first; the stand-in models are deterministic, so the take will match.
- Keep the mouse still while text is on screen; use a caption track if you skip the voice-over.
- If the recording runs long, cut the manager half of the Ask data shot first.
- For a GIF, trim the Incident shot (0:42–1:05) on its own; it carries the story.
