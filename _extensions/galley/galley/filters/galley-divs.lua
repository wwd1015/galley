-- Maps fenced divs to template environments.
-- The class -> environment table comes from document metadata
-- (galley.environments), which the extension fills from config/template.yaml,
-- so adding an environment is a config change.

local environments = {}

local function read_meta(meta)
  local galley = meta.galley
  if type(galley) ~= "table" or type(galley.environments) ~= "table" then
    return
  end
  for class, spec in pairs(galley.environments) do
    local entry = {}
    for key, value in pairs(spec) do
      entry[key] = pandoc.utils.stringify(value)
    end
    if entry.env then
      environments[class] = entry
    end
  end
end

local function wrap_div(div)
  if not FORMAT:match("latex") then
    return nil
  end
  for _, class in ipairs(div.classes) do
    local spec = environments[class]
    if spec then
      local open = "\\begin{" .. spec.env .. "}"
      local optional = spec["optional-arg"] and div.attributes[spec["optional-arg"]]
      if optional then
        open = open .. "[" .. optional .. "]"
      end
      if spec.arg then
        open = open .. "{" .. (div.attributes[spec.arg] or "") .. "}"
      end
      local blocks = pandoc.List({ pandoc.RawBlock("latex", open) })
      blocks:extend(div.content)
      blocks:insert(pandoc.RawBlock("latex", "\\end{" .. spec.env .. "}"))
      return blocks
    end
  end
  return nil
end

return {
  { Meta = read_meta },
  { Div = wrap_div },
}
