-- atomic_complete.lua
-- Atomically remove a completed task from the running set AND delete its
-- heartbeat key. Both operations happen or neither does.
--
-- This ordering prevents a race where the reconciler observes a task's
-- ID in the running set but its heartbeat key already deleted and
-- incorrectly classifies it as abandoned.
--
-- KEYS[1] = queue:{queue}:running           (set)
-- KEYS[2] = queue:task:{task_id}:heartbeat  (string)
-- ARGV[1] = task_id                         (member to SREM)
--
-- Returns: 1 (always -- idempotent; no error if keys don't exist)

redis.call('SREM', KEYS[1], ARGV[1])
redis.call('DEL', KEYS[2])
return 1
