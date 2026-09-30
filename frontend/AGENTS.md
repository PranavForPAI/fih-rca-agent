<!-- LOVABLE:BEGIN -->
> [!IMPORTANT]
> This project is connected to [Lovable](https://lovable.dev). Avoid rewriting
> published git history — force pushing, or rebasing/amending/squashing commits
> that are already pushed — as it rewrites history on Lovable's side and the
> user will likely lose their project history.
>
> Commits you push to the connected branch sync back to Lovable and show up in
> the editor, so keep the branch in a working state.
<!-- LOVABLE:END -->

## Project architecture

- Keep commerce features in small `src/components/fhi` modules and shared domain data in `src/lib`; this preserves single-responsibility handoff boundaries.
- Investigation threads use route-derived IDs and browser localStorage, while all AI requests stream through the server-only `/api/chat` boundary.
- The app shell owns the viewport height and keeps navigation fixed while only the main workspace scrolls, preserving consistent desktop navigation.
