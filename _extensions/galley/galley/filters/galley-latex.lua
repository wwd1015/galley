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

return {
  { Blocks = join_caption_row_end },
}
