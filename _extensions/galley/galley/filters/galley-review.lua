-- Renders review comments as numbered margin notes.
--
-- `galley review` writes a temporary copy of the document with an anchor
-- span at each commented line:
--   []{.galley-comment ref="c123" n="1" initials="WW" state="open"}
-- This filter is inert unless the document is rendered for review
-- (metadata `galley-review: true`, set by the `review` profile).

local active = false

local LATEX = [[
\makeatletter
\@ifpackageloaded{xcolor}{}{\usepackage{xcolor}}
\definecolor{galleyopen}{HTML}{9A6700}
\definecolor{galleyopenbg}{HTML}{FFF8C5}
\definecolor{galleyresolved}{HTML}{1A7F37}
\definecolor{galleyresolvedbg}{HTML}{DAFBE1}
% #1 number, #2 initials, #3 open|resolved
\newcommand{\galleynote}[3]{%
  \textsuperscript{\sffamily\bfseries\color{galley#3}[#1]}%
  \ifinner\else
    \marginpar{\raggedright\setlength{\fboxsep}{2pt}%
      \fcolorbox{galley#3}{galley#3bg}{\scriptsize\sffamily\bfseries\color{galley#3}#1\,·\,#2}}%
  \fi}
\makeatother
]]

local HTML = [[
<style>
.galley-note { cursor: pointer; font: 600 11px/1.4 -apple-system, "Segoe UI", sans-serif;
  border-radius: 10px; padding: 1px 7px; margin-left: 4px; white-space: nowrap;
  vertical-align: 2px; border: 1px solid; }
.galley-note.galley-open { color: #9a6700; background: #fff8c5; border-color: #d4a72c; }
.galley-note.galley-resolved { color: #1a7f37; background: #dafbe1; border-color: #4ac26b; }
.galley-note.galley-flash { outline: 3px solid #0969da; }
@media (min-width: 900px) {
  .galley-note { float: right; clear: right; margin-right: -84px; width: 68px;
    text-align: center; }
}
</style>
]]

local function read_meta(meta)
  local flag = meta["galley-review"]
  active = flag == true or (flag ~= nil and pandoc.utils.stringify(flag) == "true")
  if not active then
    return nil
  end
  local format = FORMAT:match("latex") and "latex" or (FORMAT:match("html") and "html" or nil)
  if not format then
    return nil
  end
  local block = pandoc.RawBlock(format, format == "latex" and LATEX or HTML)
  local includes = meta["header-includes"]
  if includes == nil then
    meta["header-includes"] = pandoc.MetaBlocks({ block })
  elseif includes.t == "MetaList" or (type(includes) == "table" and includes[1] ~= nil and includes.t == nil) then
    table.insert(includes, pandoc.MetaBlocks({ block }))
    meta["header-includes"] = includes
  else
    meta["header-includes"] = pandoc.MetaList({ includes, pandoc.MetaBlocks({ block }) })
  end
  return meta
end

local function clean(value, pattern)
  return (value or ""):gsub(pattern, "")
end

local function note(span)
  if not span.classes:includes("galley-comment") then
    return nil
  end
  if not active then
    return {}
  end
  local number = clean(span.attributes.n, "[^%d]")
  local initials = clean(span.attributes.initials, "[^%a]")
  local state = span.attributes.state == "resolved" and "resolved" or "open"
  local ref = clean(span.attributes.ref, "[^%w%-]")
  if FORMAT:match("latex") then
    return pandoc.RawInline("latex", "\\galleynote{" .. number .. "}{" .. initials .. "}{" .. state .. "}")
  elseif FORMAT:match("html") then
    return pandoc.RawInline(
      "html",
      '<span class="galley-note galley-' .. state .. '" data-ref="' .. ref .. '" title="Comment '
        .. number .. " (" .. state .. ')">' .. number .. " · " .. initials .. "</span>"
    )
  end
  return {}
end

return {
  { Meta = read_meta },
  { Span = note },
}
