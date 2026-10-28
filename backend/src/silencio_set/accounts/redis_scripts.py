"""Short, bounded scripts for a single Redis instance (not Redis Cluster)."""

RATE_LIMIT = """
for i, key in ipairs(KEYS) do
    local count = tonumber(redis.call('GET', key) or '0')
    if count >= tonumber(ARGV[i + 1]) then
        return {0, math.max(redis.call('TTL', key), 1)}
    end
end
for _, key in ipairs(KEYS) do
    if redis.call('INCR', key) == 1 then redis.call('EXPIRE', key, ARGV[1]) end
end
return {1, 0}
"""

ISSUE_VERIFICATION = """
if redis.call('EXISTS', KEYS[2]) == 1 then return 0 end
local challenge = cjson.decode(ARGV[1])
challenge.expires_at = tonumber(redis.call('TIME')[1]) + tonumber(ARGV[2])
redis.call('SET', KEYS[1], cjson.encode(challenge), 'EX', ARGV[2])
redis.call('SET', KEYS[2], '1', 'EX', ARGV[3])
return challenge.expires_at
"""

EXCHANGE_VERIFICATION = """
local raw = redis.call('GET', KEYS[1])
if not raw then return nil end
local challenge = cjson.decode(raw)
if tonumber(redis.call('TIME')[1]) >= challenge.expires_at then
    redis.call('DEL', KEYS[1])
    return nil
end
if challenge.verification_id ~= ARGV[1] then return nil end
if challenge.code_digest ~= ARGV[2] then
    challenge.failures = challenge.failures + 1
    if challenge.failures >= tonumber(ARGV[3]) then
        redis.call('DEL', KEYS[1])
    else
        redis.call('SET', KEYS[1], cjson.encode(challenge), 'KEEPTTL')
    end
    return nil
end
local now = tonumber(redis.call('TIME')[1])
local grant = {
    email=challenge.email, email_normalized=challenge.email_normalized,
    verified_at=now, expires_at=now + tonumber(ARGV[4])
}
local encoded = cjson.encode(grant)
if not redis.call('SET', KEYS[2], encoded, 'EX', ARGV[4], 'NX') then return nil end
redis.call('DEL', KEYS[1])
return encoded
"""

ROTATE_SESSION = """
if redis.call('EXISTS', KEYS[1]) == 0 then return 0 end
if not redis.call('SET', KEYS[2], ARGV[1], 'EX', ARGV[2], 'NX') then return 0 end
redis.call('DEL', KEYS[1])
return 1
"""
