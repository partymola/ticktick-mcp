# ticktick-mcp

MCP server for TickTick task management. Create, update, complete, move, and filter tasks via the TickTick v2 API, with field-preserving updates, day-of-week date validation, read-after-write verification, and idempotent completion tracking.

Unofficial. Not affiliated with TickTick Ltd.

Built on [`ticktick-py`](https://github.com/partymola/ticktick-py) (MIT).

## Listing tasks: compact by default

The list-returning tools -- `ticktick_get_tasks_from_project` and
`ticktick_filter_tasks` -- default to `detail="compact"`. Compact output keeps
the browsing-relevant fields (`id`, `projectId`, `title`, `dueDate`,
`startDate`, `priority`, `status`, `isAllDay`, `timeZone`, `tags`) plus a
`contentPreview` (the first ~200 chars of `content`), and drops the heavy
`content`/`desc`/checklist `items` blobs and bulky sync metadata. This keeps
large projects under the MCP result-size cap -- a 65-task project drops from
~76k to ~32k characters -- so the client does not have to spill the result to
disk. Keyword search still works against `title` and `contentPreview`.

- Need the full objects? Pass `detail="full"`.
- Need one task's full content? Use `ticktick_get_by_id`.
- **Editing a task:** fetch the full object with `ticktick_get_by_id` first,
  then send every field back via `ticktick_update_task`. The TickTick API wipes
  any field omitted from an update, so compact output must never feed an update.

If a compact result would still exceed the size budget, the soonest-due tasks
are returned and a final `_truncation_note` element reports how many were
omitted -- nothing is dropped silently. Reach the rest with a narrower
`ticktick_filter_tasks` query, `detail="full"`, or `ticktick_get_by_id`.

## License

GPL-3.0-or-later. See [LICENSE](LICENSE).
