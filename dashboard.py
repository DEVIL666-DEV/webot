from flask import Flask, render_template_string
import asyncpg, asyncio, os

app = Flask(__name__)

DB_URL = os.getenv("DATABASE_URL")

TEMPLATE = """
<h1>📊 Whiteout Bot Dashboard</h1>
<p>Showing the latest 20 messages.</p>
<table border="1" cellpadding="5">
<tr><th>Guild ID</th><th>Channel ID</th><th>Author ID</th><th>Message</th></tr>
{% for m in messages %}
<tr>
  <td>{{m['guild_id']}}</td>
  <td>{{m['channel_id']}}</td>
  <td>{{m['author_id']}}</td>
  <td>{{m['content']}}</td>
</tr>
{% endfor %}
</table>
"""

async def get_messages():
    conn = await asyncpg.connect(DB_URL)
    rows = await conn.fetch("SELECT * FROM messages ORDER BY id DESC LIMIT 20;")
    await conn.close()
    return rows

@app.route('/')
def home():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    messages = loop.run_until_complete(get_messages())
    return render_template_string(TEMPLATE, messages=messages)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
