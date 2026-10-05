-- Runs after Quarto has rendered floats to LaTeX and removes artefacts that
-- would shift the typeset output away from the template.

local function is_raw_latex(inline, prefix)
  return inline ~= nil
    and inline.t == "RawInline"
    and inline.format:match("tex") ~= nil
    and inline.text:sub(1, #prefix) == prefix
end

-- Quarto wraps the caption's inlines in a Span.
local function first_leaf(inline)
  while inline ~= nil and inline.t == "Span" do
    inline = inline.content[1]
  end
  return inline
end

local function is_text_block(block)
  return block.t == "Plain" or block.t == "Para"
end

-- Quarto emits a longtable caption and its `\tabularnewline` as separate
-- paragraphs. TeX sets the gap as a space, which pushes the caption
-- off-centre, so join the row end onto the caption.
local function join_caption_row_end(blocks)
  local out = pandoc.List()
  for _, block in ipairs(blocks) do
    local previous = out[#out]
    if
      previous
      and is_text_block(previous)
      and is_text_block(block)
      and #block.content == 1
      and is_raw_latex(block.content[1], "\\tabularnewline")
      and is_raw_latex(first_leaf(previous.content[1]), "\\caption")
    then
      previous.content:insert(block.content[1])
    else
      out:insert(block)
    end
  end
  return out
end

-- Quarto ignores `tbl-pos` for tables printed by code chunks as raw LaTeX,
-- so they float away from where the author put them.
local table_position = nil

local function read_meta(meta)
  if meta["tbl-pos"] then
    table_position = pandoc.utils.stringify(meta["tbl-pos"])
  end
end

local function place_table(raw)
  if not table_position or not raw.format:match("tex") then
    return nil
  end
  local opening = "\\begin{table}"
  local text = raw.text
  if text == opening then
    text = opening .. "[" .. table_position .. "]"
  else
    text = text:gsub("\\begin{table}(%s)", "\\begin{table}[" .. table_position .. "]%1")
  end
  if text == raw.text then
    return nil
  end
  if raw.t == "RawInline" then
    return pandoc.RawInline(raw.format, text)
  end
  return pandoc.RawBlock(raw.format, text)
end

return {
  { Meta = read_meta },
  { RawBlock = place_table, RawInline = place_table },
  { Blocks = join_caption_row_end },
}
