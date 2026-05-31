import os
import re
import time
from playwright.sync_api import sync_playwright

def tex_to_html(tex_path):
    with open(tex_path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Extract Title
    title_match = re.search(r'\\title\{\\textbf\{([^}]+)\}\s*\\\\\s*\\large\s*([^}]+)\}', content)
    if title_match:
        title = title_match.group(1) + "<br><span style='font-size: 13pt; font-weight: normal;'>" + title_match.group(2) + "</span>"
    else:
        title_match = re.search(r'\\title\{([^}]+)\}', content)
        title = title_match.group(1) if title_match else "ACRE: Algebraic Concept Reasoning Engine"

    # 2. Extract Author
    author_match = re.search(r'\\author\{([^}]+)\}', content, re.DOTALL)
    author_text = author_match.group(1) if author_match else "4QDR AI Research Team"
    # Format author block safely
    author_text = author_text.replace("\\and", "<br>and<br>")
    author_text = author_text.replace("\\texttt{", "<span style='font-family: monospace;'>")
    author_text = author_text.replace("}", "</span>")
    author_text = author_text.replace("\\\\", "<br>")

    # 3. Extract Abstract
    abstract_match = re.search(r'\\begin\{abstract\}(.*?)\\end\{abstract\}', content, re.DOTALL)
    abstract = abstract_match.group(1).strip() if abstract_match else ""
    # Remove latex syntax in abstract
    abstract = abstract.replace("vs.\\", "vs.")
    abstract = abstract.replace("SCAN", "<strong>SCAN</strong>")

    # 4. Extract Main Body
    body_start = content.find(r"\maketitle") + len(r"\maketitle")
    body_end = content.find(r"\bibliographystyle")
    if body_end == -1:
        body_end = content.find(r"\end{document}")
    
    body = content[body_start:body_end].strip()
    # Remove abstract from body to prevent 'Unknown environment abstract' rendering bug
    body = re.sub(r'\\begin\{abstract\}(.*?)\\end\{abstract\}', '', body, flags=re.DOTALL)

    # 5. Extract Bibliography
    bib_match = re.search(r'\\begin\{thebibliography\}\{\d+\}(.*?)\\end\{thebibliography\}', content, re.DOTALL)
    bib_content = bib_match.group(1).strip() if bib_match else ""

    # Parse body lines and reconstruct clean HTML using lambdas to prevent bad escapes
    body = re.sub(r'\\section\{([^}]+)\}', lambda m: f'<div class="section-title">{m.group(1)}</div>', body)
    body = re.sub(r'\\subsection\{([^}]+)\}', lambda m: f'<div class="subsection-title">{m.group(1)}</div>', body)
    body = re.sub(r'\\subsubsection\{([^}]+)\}', lambda m: f'<div class="subsubsection-title">{m.group(1)}</div>', body)
    
    # Emphasis
    body = re.sub(r'\\textbf\{([^}]+)\}', lambda m: f'<strong>{m.group(1)}</strong>', body)
    body = re.sub(r'\\emph\{([^}]+)\}', lambda m: f'<em>{m.group(1)}</em>', body)
    body = re.sub(r'\\texttt\{([^}]+)\}', lambda m: f'<code>{m.group(1)}</code>', body)
    
    # Spacing and accents
    body = body.replace(r'$-$', '&ndash;')
    body = body.replace(r'--', '&ndash;')
    body = body.replace(r'---', '&mdash;')
    body = body.replace(r'``', '&ldquo;')
    body = body.replace(r"''", '&rdquo;')
    body = body.replace(r'vs.\ ', 'vs. ')
    body = body.replace(r'\euro', '&euro;')
    
    # Lists
    body = re.sub(r'\\begin\{enumerate\}(?:\[[^\]]+\])?', r'<ol>', body)
    body = body.replace(r'\end{enumerate}', r'</ol>')
    body = re.sub(r'\\begin\{itemize\}(?:\[[^\]]+\])?', r'<ul>', body)
    body = body.replace(r'\end{itemize}', r'</ul>')
    body = body.replace(r'\item', r'<li>')
    
    # Equations
    body = re.sub(r'\\begin\{equation\}(.*?)\\end\{equation\}', lambda m: f'<div class="equation-container">$$\\begin{{equation}}{m.group(1)}\\end{{equation}}$$</div>', body, flags=re.DOTALL)
    body = re.sub(r'\\begin\{align\}(.*?)\\end\{align\}', lambda m: f'<div class="equation-container">$$\\begin{{align}}{m.group(1)}\\end{{align}}$$</div>', body, flags=re.DOTALL)
    
    # Theorems and Proofs
    body = re.sub(r'\\begin\{theorem\}(?:\[([^\]]+)\])?(.*?)\\end\{theorem\}', 
                  lambda m: f'<div class="theorem-box"><div class="theorem-title">Theorem ({m.group(1)})</div><div class="theorem-body">{m.group(2)}</div></div>' if m.group(1) else f'<div class="theorem-box"><div class="theorem-title">Theorem</div><div class="theorem-body">{m.group(2)}</div></div>', 
                  body, flags=re.DOTALL)
    body = re.sub(r'\\begin\{lemma\}(?:\[([^\]]+)\])?(.*?)\\end\{lemma\}', 
                  lambda m: f'<div class="theorem-box"><div class="theorem-title">Lemma ({m.group(1)})</div><div class="theorem-body">{m.group(2)}</div></div>' if m.group(1) else f'<div class="theorem-box"><div class="theorem-title">Lemma</div><div class="theorem-body">{m.group(2)}</div></div>', 
                  body, flags=re.DOTALL)
    body = re.sub(r'\\begin\{definition\}(?:\[([^\]]+)\])?(.*?)\\end\{definition\}', 
                  lambda m: f'<div class="theorem-box"><div class="theorem-title">Definition ({m.group(1)})</div><div class="theorem-body">{m.group(2)}</div></div>' if m.group(1) else f'<div class="theorem-box"><div class="theorem-title">Definition</div><div class="theorem-body">{m.group(2)}</div></div>', 
                  body, flags=re.DOTALL)
    body = re.sub(r'\\begin\{proof\}(.*?)\\end\{proof\}', lambda m: f'<p class="no-indent"><strong>Proof.</strong> {m.group(1)} <span style="float: right;">$\\square$</span></p>', body, flags=re.DOTALL)
    
    # Citations and labels
    body = re.sub(r'\\cite\{([^}]+)\}', lambda m: f'[{m.group(1)}]', body)
    body = re.sub(r'\\citep\{([^}]+)\}', lambda m: f'({m.group(1)})', body)
    body = re.sub(r'\\citet\{([^}]+)\}', lambda m: f'{m.group(1)}', body)
    body = re.sub(r'\\ref\{([^}]+)\}', lambda m: f'<span class="latex-ref">{m.group(1)}</span>', body)
    body = re.sub(r'\\label\{([^}]+)\}', r'', body)
    body = re.sub(r'\\appendix', lambda m: r'<hr><div class="section-title" style="text-align: center; border: none; font-size: 16pt;">Appendix</div>', body)
    
    # Cleanup LaTeX structures like tables and figures to render perfectly in HTML
    # Let's parse tables using basic regex
    def convert_table(match):
        table_label = re.search(r'\\label\{([^}]+)\}', match.group(0))
        table_caption = re.search(r'\\caption\{([^}]+)\}', match.group(0))
        caption_html = f"<div class='table-caption'>Table: {table_caption.group(1)}</div>" if table_caption else ""
        
        tabular_content = re.search(r'\\begin\{tabular\}\{[^}]+\}(.*?)\\end\{tabular\}', match.group(0), re.DOTALL)
        if not tabular_content:
            return ""
        
        rows = tabular_content.group(1).strip().split(r'\\')
        html_rows = []
        for row in rows:
            row = row.strip()
            if not row or row.startswith(r'\toprule') or row.startswith(r'\midrule') or row.startswith(r'\bottomrule') or row.startswith(r'\hline'):
                continue
            cols = row.split('&')
            html_cols = []
            for col in cols:
                col = col.strip()
                # Determine header row if bold
                if "textbf" in col:
                    col_clean = re.sub(r'\\textbf\{([^}]+)\}', lambda m: m.group(1), col)
                    html_cols.append(f"<th>{col_clean}</th>")
                else:
                    html_cols.append(f"<td>{col}</td>")
            html_rows.append("<tr>" + "".join(html_cols) + "</tr>")
            
        return f"{caption_html}<table>" + "".join(html_rows) + "</table>"
        
    body = re.sub(r'\\begin\{table\}(.*?)\\end\{table\}', convert_table, body, flags=re.DOTALL)
    
    # Clean up empty lines or raw comments
    body_lines = []
    in_comment_block = False
    for line in body.split('\n'):
        line_strip = line.strip()
        if line_strip.startswith('%'):
            continue
        body_lines.append(line)
        
    body = "\n".join(body_lines)

    # 6. Parse References
    bib_items_html = []
    for item in bib_content.strip().split(r'\bibitem'):
        item = item.strip()
        if not item:
            continue
        # Extract cite key and authors
        cite_match = re.match(r'\[([^\]]+)\]\{([^}]+)\}(.*)', item, re.DOTALL)
        if cite_match:
            label, key, citation = cite_match.groups()
            citation = citation.strip()
            citation = re.sub(r'\\emph\{([^}]+)\}', lambda m: f'<em>{m.group(1)}</em>', citation)
            citation = citation.replace(r"\'e", "&eacute;")
            citation = citation.replace(r"\`e", "&egrave;")
            citation = citation.replace(r"\^e", "&ecirc;")
            citation = citation.replace(r"\`a", "&agrave;")
            citation = citation.replace(r"\"o", "&ouml;")
            citation = citation.replace(r"\"a", "&auml;")
            citation = citation.replace(r"\"u", "&uuml;")
            citation = citation.replace(r"{\L}", "L")
            bib_items_html.append(f"<div class='reference-item'><strong>[{label}]</strong> {citation}</div>")

    references_html = ""
    if bib_items_html:
        references_html = "<div class='section-title'>References</div><div class='references-list'>" + "".join(bib_items_html) + "</div>"

    # Assemble HTML
    html_content = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>ACRE: Algebraic Concept Reasoning Engine</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/latin-modern-webfonts@1.0.0/style.css">
<script>
  window.MathJax = {{
    tex: {{
      inlineMath: [['$', '$'], ['\\\\(', '\\\\)']],
      displayMath: [['$$', '$$'], ['\\\\[', '\\\\]']],
      processEscapes: true,
      macros: {{
        R: "{{\\\\mathbb{{R}}}}",
        E: "{{\\\\mathbb{{E}}}}",
        cC: "{{\\\\mathcal{{C}}}}",
        cP: "{{\\\\mathcal{{P}}}}",
        cS: "{{\\\\mathcal{{S}}}}",
        cO: "{{\\\\mathcal{{O}}}}",
        cV: "{{\\\\mathcal{{V}}}}",
        cK: "{{\\\\mathcal{{K}}}}",
        cB: "{{\\\\mathcal{{B}}}}",
        cM: "{{\\\\mathcal{{M}}}}",
        bW: "{{\\\\mathbf{{W}}}}",
        bc: "{{\\\\mathbf{{c}}}}",
        bp: "{{\\\\mathbf{{p}}}}",
        bs: "{{\\\\mathbf{{s}}}}",
        bI: "{{\\\\mathbf{{I}}}}",
        llbracket: "{{\\\\lbrack\\\\!\\\\lbrack}}",
        rrbracket: "{{\\\\rbrack\\\\!\\\\rbrack}}"
      }}
    }},
    startup: {{
      pageReady: () => {{
        return MathJax.startup.defaultPageReady().then(() => {{
          window.mathjaxReady = true;
        }});
      }}
    }}
  }};
</script>
<script id="MathJax-script" async src="https://cdn.jsdelivr.net/npm/mathjax@3/es5/tex-mml-chtml.js"></script>
<style>
  @page {{
    size: A4;
    margin: 2.54cm;
  }}
  body {{
    font-family: 'Latin Modern Roman', 'Times New Roman', serif;
    font-size: 10pt;
    line-height: 1.5;
    color: #000;
    margin: 0;
    padding: 0;
    background-color: #fff;
    text-align: justify;
  }}
  .paper-title {{
    font-size: 16pt;
    font-weight: bold;
    text-align: center;
    margin-top: 1cm;
    margin-bottom: 0.6cm;
    line-height: 1.25;
  }}
  .authors {{
    text-align: center;
    font-size: 11pt;
    margin-bottom: 0.8cm;
    line-height: 1.4;
  }}
  .abstract-container {{
    margin: 0 1.2cm 0.8cm 1.2cm;
    font-size: 9.5pt;
    line-height: 1.45;
    background-color: #fdfdfd;
    border: 0.5px solid #ccc;
    padding: 15px;
    border-radius: 4px;
  }}
  .abstract-title {{
    font-weight: bold;
    text-align: center;
    margin-bottom: 5px;
    font-size: 10pt;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }}
  .section-title {{
    font-size: 12pt;
    font-weight: bold;
    margin-top: 25px;
    margin-bottom: 10px;
    border-bottom: 0.5px solid #000;
    padding-bottom: 3px;
  }}
  .subsection-title {{
    font-size: 11pt;
    font-weight: bold;
    margin-top: 18px;
    margin-bottom: 8px;
  }}
  .subsubsection-title {{
    font-size: 10pt;
    font-weight: bold;
    margin-top: 12px;
    margin-bottom: 4px;
  }}
  p {{
    margin: 0 0 10px 0;
    text-indent: 1.5em;
  }}
  .no-indent {{
    text-indent: 0 !important;
  }}
  ul, ol {{
    margin: 6px 0;
    padding-left: 20px;
  }}
  li {{
    margin-bottom: 5px;
  }}
  .equation-container {{
    display: flex;
    align-items: center;
    justify-content: center;
    margin: 14px 0;
    width: 100%;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    margin: 15px 0;
    font-size: 9pt;
  }}
  th {{
    border-top: 1.5px solid #000;
    border-bottom: 1px solid #000;
    padding: 6px 8px;
    font-weight: bold;
    text-align: left;
  }}
  td {{
    border-bottom: 1px solid #ddd;
    padding: 5px 8px;
    text-align: left;
  }}
  tr:last-child td {{
    border-bottom: 1.5px solid #000;
  }}
  .table-caption {{
    font-size: 8.5pt;
    font-weight: bold;
    text-align: center;
    margin-bottom: 5px;
  }}
  .theorem-box {{
    border: 0.5px solid #000;
    background-color: #fafafa;
    padding: 10px 12px;
    margin: 14px 0;
    font-size: 9.5pt;
  }}
  .theorem-title {{
    font-weight: bold;
    margin-bottom: 3px;
  }}
  .theorem-body {{
    font-style: italic;
  }}
  .references-list {{
    margin-top: 15px;
  }}
  .reference-item {{
    padding-left: 20px;
    text-indent: -20px !important;
    margin-bottom: 8px;
    font-size: 9pt;
    line-height: 1.35;
  }}
</style>
</head>
<body>
  <div class="paper-title">{title}</div>
  <div class="authors">{author_text}</div>
  
  <div class="abstract-container">
    <div class="abstract-title">Abstract</div>
    <div style="font-style: italic;">{abstract}</div>
  </div>
  
  <div class="no-indent">
    {body}
  </div>
  
  {references_html}
</body>
</html>
"""
    return html_content

def compile_pdf():
    print("=== ACRE: Compiling LaTeX scientific paper to PDF ===")
    
    tex_path = os.path.abspath("docs/scientific_paper.tex")
    html_path = os.path.abspath("docs/scientific_paper.html")
    pdf_path = os.path.abspath("docs/scientific_paper.pdf")
    
    if not os.path.exists(tex_path):
        print(f"Error: {tex_path} not found.")
        return
        
    print("Parsing scientific_paper.tex and building HTML...")
    html_content = tex_to_html(tex_path)
    with open(html_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    print(f"Successfully generated HTML draft at: {html_path}")
    
    print("Launching Playwright to compile PDF...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            viewport={"width": 800, "height": 600},
            device_scale_factor=2
        )
        page = context.new_page()
        file_url = f"file:///{html_path.replace(os.sep, '/')}"
        page.goto(file_url)
        page.emulate_media(media="print")
        
        print("Waiting for MathJax typesetter...")
        page.wait_for_function("window.mathjaxReady === true", timeout=30000)
        time.sleep(1.5)  # Let layout settle
        
        print("Printing to A4 PDF...")
        page.pdf(
            path=pdf_path,
            print_background=True,
            format="A4",
            margin={"top": "2.54cm", "bottom": "2.54cm", "left": "2.54cm", "right": "2.54cm"}
        )
        browser.close()
        
    print(f"PDF successfully generated and saved to: {pdf_path}")
    
    # Clean up intermediate HTML
    if os.path.exists(html_path):
        os.remove(html_path)

if __name__ == "__main__":
    compile_pdf()
