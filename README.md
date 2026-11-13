# Neativerse

A bulletin board of stories dealt from a handful of curated feeds.

## Layout

    feeds.json                    the feeds on the board, and each one's colour
    build.py                      reads the feeds, writes site/pool.json (Python standard library only)
    site/index.html               the board itself
    site/pool.json                the pool of recent stories (written by build.py)
    .github/workflows/build.yml   runs build.py every two hours and commits the pool when it changes

## Setting it up

1. Create a repository (public or private) and push these files to it.
2. On GitHub, open the Actions tab and run "Build pool" once by hand. It writes and commits
   `site/pool.json`. If the push is refused, set Settings > Actions > General > Workflow
   permissions to "Read and write".
3. In Cloudflare Pages, create a project connected to the repository: no build command,
   output directory `site`. Every pool commit then redeploys the board.

To try it locally:

    python build.py
    python -m http.server -d site 8000

## How the board behaves

- The pool holds each feed's stories from the last 14 days (`window_days`), up to 120 per feed.
  A story stays in the pool for the whole window even after it drops off the feed.
- Each visit deals 30 cards, split evenly between the feeds. Newer stories are more likely to be
  dealt and to land near the top; stories marked seen come up far less often.
- "Shuffle" deals a fresh board. "Deal more" adds cards without repeating any.
- Opening a story marks it seen. Seen marks live in the visitor's browser only.
- If a feed can't be read, its earlier stories stay on the board until it comes back.

The dials (cards per deal, how fast stories age, how rarely seen stories return) are the
constants at the top of the script in `site/index.html`.

## Adding a feed

Add an entry to `feeds.json` with a new `id`, the feed's `name`, `site`, `feed` URL and a
`colour`: orange, purple, teal, green, blue or rose.
