-- guarded_delete.lua
-- Delete a task hash ONLY while its status is one of the allowed values
-- (cancel_by_metadata's queued-task cleanup); optionally remove the id
-- from a pending zset in the same atomic step. A task that raced to
-- running keeps its live hash — the caller falls back to a guarded
-- cancel so the handler gets the cooperative signal instead of having
-- its record destroyed mid-flight.
--
-- KEYS[1] = queue:task:{task_id}                    (hash)
-- KEYS[2] = set/zset to remove the id from ('' when unused)
-- ARGV[1] = task_id
-- ARGV[2] = comma-separated allowed current statuses
-- ARGV[3] = removal mode: "none" | "srem" | "zrem"
--
-- Returns '__ok__' on success, '__missing__' when the hash does not
-- exist, otherwise the current status (the caller lost the race).

if redis.call('EXISTS', KEYS[1]) == 0 then
  return '__missing__'
end
local current = redis.call('HGET', KEYS[1], 'status')
if current == false then current = '' end
local allowed = false
for s in string.gmatch(ARGV[2], '([^,]+)') do
  if s == current then allowed = true end
end
if not allowed then
  return current
end
if ARGV[3] == 'srem' then
  redis.call('SREM', KEYS[2], ARGV[1])
elseif ARGV[3] == 'zrem' then
  redis.call('ZREM', KEYS[2], ARGV[1])
end
redis.call('DEL', KEYS[1])
return '__ok__'
