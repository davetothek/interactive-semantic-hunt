-- contrib/nvim/tests/test_source.lua
-- Check the choice between this tree and a server, with stand-ins for both.
--
-- Run it from the root of the repository:
--
--   nvim --headless --clean -c "luafile contrib/nvim/tests/test_source.lua" -c q
--
-- It needs neither ish nor fzf-lua. It prints one line for each check, and
-- exits with an error when one fails.

local here = vim.fn.fnamemodify(debug.getinfo(1, 'S').source:sub(2), ':p:h:h')
for _, name in ipairs({ 'ish', 'ish_server', 'ish_ask', 'ish_link' }) do
  package.preload['utils.' .. name] = function()
    return dofile(here .. '/' .. name .. '.lua')
  end
end
local failures = 0
local function check(name, got, want)
  if vim.deep_equal(got, want) then
    print('ok   ' .. name)
  else
    failures = failures + 1
    print('FAIL ' .. name .. ': got ' .. vim.inspect(got) .. ', want ' .. vim.inspect(want))
  end
end

local server = require('utils.ish_server')
local asked_to_build = 0
local function setup(has, up, agree)
  asked_to_build = 0
  server.indexed = {}
  server.build_answers = {}
  server.local_server.exists = function(_, cb) cb(has) end
  server.remote.available = function(cb) cb(up) end
  server._agree_to_build = function() asked_to_build = asked_to_build + 1; return agree end
end
local function choose(mode)
  local got = 'not called'
  server.choose('/tree', mode, function(choice) got = choice end)
  return got
end

setup(true, true, false)
check('auto, both: both', choose('auto'), { use_local = true, use_remote = true })
check('auto, both: no question', asked_to_build, 0)
setup(false, true, false)
check('auto, server only: server', choose(nil), { use_local = false, use_remote = true })
check('auto, server only: no question, no local tree', { asked_to_build, server.indexed['/tree'] }, { 0, nil })
setup(true, false, false)
check('auto, local only: local', choose('auto'), { use_local = true, use_remote = false })
check('auto, local only: the tree is marked', server.indexed['/tree'], true)
setup(false, false, true)
check('auto, neither, agree: local', choose('auto'), { use_local = true, use_remote = false })
check('auto, neither: asked once', asked_to_build, 1)
setup(false, false, false)
check('auto, neither, refuse: nothing', choose('auto'), nil)
check('auto, neither, refuse: no tree marked', server.indexed['/tree'], nil)
setup(false, true, false)
check('local, no index, refuse: nothing', choose('local'), nil)
check('local, no index: asked', asked_to_build, 1)
setup(true, true, false)
check('local, index: local only', choose('local'), { use_local = true, use_remote = false })
setup(true, true, false)
check('remote, server: server only', choose('remote'), { use_local = false, use_remote = true })
setup(true, false, false)
server.remote.last_error = 'No server is set. Give --url URL, or set ISH_REMOTE.'
local said = {}
local notify = vim.notify
vim.notify = function(message) table.insert(said, message) end
check('remote, no server: nothing', choose('remote'), nil)
vim.notify = notify
check('remote, no server: says why', said, { 'ish: No server is set. Give --url URL, or set ISH_REMOTE.' })

-- The question comes once for each tree, whatever the answer.
setup(false, false, false)
choose('auto')
choose('auto')
check('a no is asked once and stands', { asked_to_build, choose('auto') }, { 1, nil })
setup(false, false, true)
choose('auto')
check('a yes is asked once and stands', { choose('auto'), asked_to_build }, {
  { use_local = true, use_remote = false },
  1,
})
setup(false, false, false)
choose('auto')
server.choose('/other', 'auto', function() end)
check('each tree is asked for itself', asked_to_build, 2)

-- The picker, without fzf-lua: the mark and the order.
package.loaded['fzf-lua'] = { utils = {}, make_entry = {}, defaults = { actions = {} } }
local ish = require('utils.ish')
local mark = ish._internal.mark_remote
check('mark after a rank', mark('docs/a.md:3:1:[0.71] section A'), 'docs/a.md:3:1:[0.71] [server] section A')
check('mark with no rank', mark('docs/a.md:3:1: section A'), 'docs/a.md:3:1:[server] section A')
check('no location: unchanged', mark('No results'), 'No results')
local fake = {
  search = function(_, _, cb) cb({ 'here.py:1:1:[0.50] function here' }) end,
  remote = { search = function(_, _, cb) cb({ 'there.py:2:1:[0.90] function there' }) end },
}
local lines
ish._internal.search_all(fake, { use_local = true, use_remote = true }, 'q', {}, function(got) lines = got end)
check('both: this tree first, then the server', lines, {
  'here.py:1:1:[0.50] function here',
  'there.py:2:1:[0.90] [server] function there',
})
ish._internal.search_all(fake, { use_local = false, use_remote = true }, 'q', {}, function(got) lines = got end)
check('server only: marked', lines, { 'there.py:2:1:[0.90] [server] function there' })

-- Building by name marks the tree, so later searches and saves use it.
setup(false, false, false)
choose('auto')
local refreshed = {}
server.ensure = function() return true end
server.refresh = function(cwd) table.insert(refreshed, cwd) end
-- The watch asks for the status once. A server with nothing to say ends it.
server.status = function(_, cb) cb({}) end
ish.index({ cwd = '/tree' })
check('index by name refreshes the tree', refreshed, { '/tree' })
check('index by name marks the tree', { server.indexed['/tree'], server.build_answers['/tree'] }, { true, true })
server.local_server.exists = function(_, cb) cb(false) end
check('a later search reads this tree, unasked', { choose('auto'), asked_to_build }, {
  { use_local = true, use_remote = false },
  1,
})

local ask = require('utils.ish_ask')
check('remote command names no path', ask._internal.remote_command('-q is what', { args = { '--brief' } }),
  { 'ish', 'remote', 'ask', '--brief', '--', '-q is what' })
check('local command as before', ask._internal.command('why', { cwd = '/tree' }), { 'ish', 'ask', '--', 'why', '/tree' })

print(failures == 0 and 'ALL PASSED' or (failures .. ' FAILED'))
if failures > 0 then vim.cmd('cq') end
