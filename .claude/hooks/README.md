# Secret-path guard

`guard.sh` runs before `Edit`, `Write` and `Bash`. File writes must stay inside
the project. All three tools reject direct access to `.env`, `.env.*`, `*.env`,
`*.runner`, `*.pem`, `*.key`, `id_rsa*` and paths inside a `secrets` directory,
including symlink targets. The `.env.example` Git ignore exception does not relax
this tool guard.

For Bash, literal paths are checked after shell quote parsing, including
redirection targets and paths embedded in wrapper arguments. Ordinary commands
such as `uv run pytest tests/unit/`, `uv run ruff check .` and `git status --short`
remain usable. Commands with unresolved variable or command substitutions,
globs, pipe-fed arguments, `eval` or `find -exec` are blocked; use explicit paths.

This hook is a defence against direct path access and simple shell bypasses,
not a sandbox. It cannot prove what arbitrary repository scripts or interpreted
programs will read, write or construct at runtime. Do not place real credentials
in an agent workspace; filesystem isolation remains necessary for stronger
protection. Block messages for Bash never echo command text or file contents.

Run `bash tests/hooks/test_guard.sh`, `bash tests/hooks/test_settings.sh` and
`bash tests/hooks/test_gitignore.sh` to check the guard, its registration and the
Git ignore rules.
