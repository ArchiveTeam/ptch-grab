local url_count = 0


wget.callbacks.download_child_p = function(urlpos, parent, depth, start_url_parsed, iri, verdict, reason)
  local url = urlpos["url"]["url"]

  -- Don't go to url templates
  if string.match(url, "{{") then
    return false
  end

  return verdict
end


wget.callbacks.get_urls = function(file, url, is_css, iri)
  -- progress message
  url_count = url_count + 1
  if url_count % 2 == 0 then
    io.stdout:write("\r - Downloaded "..url_count.." URLs.")
    io.stdout:flush()
  end
end

