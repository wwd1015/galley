-- Restyles Pandoc tables to the template's conventions.
-- Settings come from document metadata (galley.tables), which the extension
-- fills from config/template.yaml. Pandoc already emits booktabs rules with
-- the caption above; this filter applies what the config adds on top.

local settings = {}

local function read_meta(meta)
  local galley = meta.galley
  if type(galley) ~= "table" or type(galley.tables) ~= "table" then
    return
  end
  for key, value in pairs(galley.tables) do
    settings[key] = pandoc.utils.stringify(value)
  end
end

local function restyle(tbl)
  if not FORMAT:match("latex") then
    return nil
  end
  local size = settings["font-size"]
  if not size or not size:match("^%a+$") then
    return nil
  end
  return {
    pandoc.RawBlock("latex", "\\begingroup\\" .. size),
    tbl,
    pandoc.RawBlock("latex", "\\endgroup"),
  }
end

return {
  { Meta = read_meta },
  { Table = restyle },
}
