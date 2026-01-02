# AGENTS.md

## Dev Commands
- Run: `uv run tps <command>`

## Code Style Guidelines
- **Imports**: Standard library first, then third-party, alphabetical within groups
- **Formatting**: 4 spaces indentation, no trailing whitespace
- **Naming**: snake_case for functions/variables, CamelCase for classes, UPPER_CASE for constants
- **Types**: No type hints used (consider adding for new code)
- **Docstrings**: Use triple quotes for function descriptions
- **Error Handling**: Use try/except with specific exceptions, sys.exit(1) for CLI errors
- **Subprocess**: Use subprocess.run with check=True and capture_output as needed
- **Logging**: Use print() for user messages, avoid logging module unless needed
- **Security**: Expand user paths with os.path.expanduser(), handle sudo correctly for credentials

No Cursor or Copilot rules found.</content>
<parameter name="filePath">AGENTS.md
