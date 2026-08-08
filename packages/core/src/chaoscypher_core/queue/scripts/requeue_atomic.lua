-- requeue_atomic.lua
-- The reconciler's abandoned-task requeue as ONE atomic move: reset the
-- hash, clear any dead-letter TTL, add to pending, remove from running,
-- bump attempts. Replaces the zadd-then-srem sequence whose trailing
-- srem could fail transiently and leave the task in BOTH sets — a
-- worker could then pull it from pending while the next reconcile cycle
-- requeues it again (duplicate execution). Refuses if the task finished
-- while being classified (completed/cancelled) — the caller then just
-- SREMs the stale running-set entry.
--
-- KEYS[1] = queue:task:{task_id}    (hash)
-- KEYS[2] = queue:{queue}:pending   (zset)
-- KEYS[3] = queue:{queue}:running   (set)
-- ARGV[1] = task_id
-- ARGV[2] = priority
--
-- Returns '__ok__' on success, '__missing__' when the hash does not
-- exist, otherwise the terminal status that blocked the requeue.

if redis.call('EXISTS', KEYS[1]) == 0 then
  return '__missing__'
end
local current = redis.call('HGET', KEYS[1], 'status')
if current == 'completed' or current == 'cancelled' then
  return current
end
redis.call('HSET', KEYS[1], 'status', 'queued', 'error', '', 'error_type', '')
redis.call('PERSIST', KEYS[1])
redis.call('ZADD', KEYS[2], tonumber(ARGV[2]), ARGV[1])
redis.call('SREM', KEYS[3], ARGV[1])
redis.call('HINCRBY', KEYS[1], 'attempts', 1)
return '__ok__'
