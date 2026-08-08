-- guarded_status_write.lua
-- Atomically transition a task hash's status ONLY while its current
-- status is one of the allowed values; optionally remove the task from a
-- pending zset / running set and apply a TTL in the same atomic step.
-- Closes the read-then-write TOCTOU family (a cancel clobbering a
-- just-completed task, retry double-enqueue, the reconciler's
-- srem-before-write terminal ordering).
--
-- KEYS[1] = queue:task:{task_id}                    (hash)
-- KEYS[2] = set/zset to remove the id from ('' when unused)
-- ARGV[1] = task_id
-- ARGV[2] = comma-separated allowed current statuses (e.g. "queued,running")
-- ARGV[3] = removal mode: "none" | "srem" | "zrem"
-- ARGV[4] = expire seconds ('' to skip)
-- ARGV[5..] = alternating field, value pairs to HSET (includes status)
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
for i = 5, #ARGV, 2 do
  redis.call('HSET', KEYS[1], ARGV[i], ARGV[i + 1])
end
if ARGV[3] == 'srem' then
  redis.call('SREM', KEYS[2], ARGV[1])
elseif ARGV[3] == 'zrem' then
  redis.call('ZREM', KEYS[2], ARGV[1])
end
if ARGV[4] ~= '' then
  redis.call('EXPIRE', KEYS[1], tonumber(ARGV[4]))
end
return '__ok__'
