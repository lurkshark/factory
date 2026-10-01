---
name: auth
kind: service
id_prefix: AUTH
depends_on: [clock]
interface: src/api.ts
preserve: [src/api.ts, src/migrations/**]
append_only: [src/migrations/**]
evals:
  run: python3 evals/run.py
  report: evals/.results/junit.xml
---

# auth

Untouched description with café and trailing spaces.  

## Specs

### AUTH-001
A login MUST return a token.
```markdown
## Specs
### AUTH-888
#### an example, not a heading
```

### AUTH-002 [quarantine]
Tokens MUST expire after a day.

## Notes

Untouched footer.
