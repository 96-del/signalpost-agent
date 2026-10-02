import json

def build_viewer():
    input_file = r"out\smoke-envelopes.jsonl"
    output_file = r"out\viewer.html"

    with open(input_file, "r", encoding="utf-8") as f:
        rows = [json.loads(line) for line in f]

    cards_html = []
    for r in rows:
        org = r.get("organisation_number", "Unknown")
        claims = r.get("claims", [])
        evidence = r.get("evidence", [])
        primary_source = evidence[0].get("source_url", "#") if evidence else "#"

        items = "".join([f"<li><strong>{c.get('field')}:</strong> {c.get('availability')}</li>" for c in claims[:7]])
        
        card = f"""
        <article class="card" data-org="{org}">
            <div class="card-header">
                <h2>Org: {org}</h2>
                <span class="badge">{len(claims)} Verified Claims</span>
            </div>
            <ul>{items}</ul>
            <div class="card-footer">
                <a href="{primary_source}" target="_blank" rel="noreferrer">Verify Primary Source &rarr;</a>
            </div>
        </article>
        """
        cards_html.append(card)

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Signalpost Verified Company Directory</title>
    <style>
        :root {{ --bg: #0f172a; --card-bg: #1e293b; --text: #f8fafc; --muted: #94a3b8; --accent: #38bdf8; --border: #334155; }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{ font-family: system-ui, -apple-system, sans-serif; background: var(--bg); color: var(--text); padding: 1.5rem; }}
        header {{ max-width: 1200px; margin: 0 auto 2rem auto; }}
        h1 {{ font-size: 1.8rem; margin-bottom: 0.5rem; }}
        p {{ color: var(--muted); margin-bottom: 1rem; font-size: 0.95rem; }}
        input {{ width: 100%; max-width: 450px; padding: 0.75rem 1rem; border-radius: 8px; border: 1px solid var(--border); background: var(--card-bg); color: #fff; font-size: 1rem; }}
        main {{ max-width: 1200px; margin: 0 auto; display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 1.25rem; }}
        .card {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 10px; padding: 1.25rem; display: flex; flex-direction: column; justify-content: space-between; }}
        .card-header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 1rem; }}
        h2 {{ font-size: 1.1rem; color: var(--accent); }}
        .badge {{ background: #059669; color: #fff; font-size: 0.75rem; padding: 3px 8px; border-radius: 9999px; font-weight: 600; }}
        ul {{ list-style: none; margin-bottom: 1.25rem; }}
        li {{ font-size: 0.85rem; color: var(--muted); margin-bottom: 0.35rem; }}
        li strong {{ color: var(--text); }}
        .card-footer a {{ color: var(--accent); text-decoration: none; font-size: 0.85rem; font-weight: 500; }}
        .card-footer a:hover {{ text-decoration: underline; }}
    </style>
</head>
<body>
    <header>
        <h1>Signalpost Corporate Intelligence</h1>
        <p>100 Verified Profiles — Fully Auditable Public Data</p>
        <input type="search" id="search" placeholder="Filter by organization number or field..." oninput="filterCards()">
    </header>
    <main id="cards">
        {''.join(cards_html)}
    </main>
    <script>
        function filterCards() {{
            const query = document.getElementById('search').value.toLowerCase();
            const cards = document.querySelectorAll('.card');
            cards.forEach(card => {{
                card.style.display = card.innerText.toLowerCase().includes(query) ? 'flex' : 'none';
            }});
        }}
    </script>
</body>
</html>
"""
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(html_content)
    print("Successfully generated out\\viewer.html")

if __name__ == "__main__":
    build_viewer()
