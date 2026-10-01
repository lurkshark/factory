---
module: auth
set_depends_on: []
---

## Motivation
Simplify login and replace expiry behavior.

## Added

### AUTH-003 [blackbox]
A logout MUST revoke the token.

## Modified

### AUTH-001 [review]
A login MUST return an opaque token.

## Removed

### AUTH-002
Expiry is no longer required.

## Decisions

### Details
Free prose headings are allowed here.
