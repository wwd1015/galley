% STAND-IN TEMPLATE. Placeholder for the real team template; replace the
% contents of template/ and update config/template.yaml when it arrives.
\NeedsTeXFormat{LaTeX2e}
\ProvidesClass{whitepaper}[2026/10/05 Stand-in whitepaper class]
\LoadClass[11pt,a4paper]{article}

\RequirePackage[a4paper,top=30mm,bottom=28mm,left=28mm,right=28mm,headheight=14pt]{geometry}
\RequirePackage{fontspec}
\setmainfont{texgyrepagella}[Extension=.otf,UprightFont=*-regular,BoldFont=*-bold,
  ItalicFont=*-italic,BoldItalicFont=*-bolditalic]
\setsansfont{texgyreheros}[Extension=.otf,UprightFont=*-regular,BoldFont=*-bold,
  ItalicFont=*-italic,BoldItalicFont=*-bolditalic]
\setmonofont{texgyrecursor}[Extension=.otf,UprightFont=*-regular,BoldFont=*-bold,
  ItalicFont=*-italic,BoldItalicFont=*-bolditalic,Scale=0.9]

\RequirePackage{xcolor}
\definecolor{wpnavy}{HTML}{14324F}
\definecolor{wpaccent}{HTML}{C8102E}
\definecolor{wpgrey}{HTML}{5F6B76}
\definecolor{wptint}{HTML}{EEF2F6}

\RequirePackage{graphicx}
\RequirePackage{booktabs,longtable,array}
\RequirePackage{float}
\RequirePackage{caption}
\captionsetup{font={small,sf},labelfont={bf,color=wpnavy},labelsep=period,
  justification=raggedright,singlelinecheck=false,skip=6pt}
\RequirePackage{enumitem}
\setlist{nosep}
\RequirePackage{tcolorbox}
\RequirePackage{fancyhdr}
\RequirePackage{titlesec}
\RequirePackage[hidelinks]{hyperref}

\setlength{\parindent}{0pt}
\setlength{\parskip}{6pt plus 2pt minus 1pt}

% Headings
\titleformat{\section}{\sffamily\Large\bfseries\color{wpnavy}}{\thesection}{0.8em}{}
\titleformat{\subsection}{\sffamily\large\bfseries\color{wpnavy}}{\thesubsection}{0.8em}{}
\titleformat{\subsubsection}{\sffamily\normalsize\bfseries\color{wpnavy}}{\thesubsubsection}{0.8em}{}
\titleformat{\paragraph}[runin]{\sffamily\normalsize\bfseries\color{wpgrey}}{}{0pt}{}[.]
\titlespacing*{\paragraph}{0pt}{6pt}{0.6em}

% Document metadata
\newcommand{\wp@subtitle}{}
\newcommand{\wp@docid}{}
\newcommand{\wp@classification}{}
\newcommand{\wp@version}{}
\newcommand{\subtitle}[1]{\renewcommand{\wp@subtitle}{#1}}
\newcommand{\docid}[1]{\renewcommand{\wp@docid}{#1}}
\newcommand{\classification}[1]{\renewcommand{\wp@classification}{#1}}
\newcommand{\docversion}[1]{\renewcommand{\wp@version}{#1}}

% Running header and footer
\pagestyle{fancy}
\fancyhf{}
\fancyhead[L]{\sffamily\small\color{wpgrey}\wp@docid}
\fancyhead[R]{\sffamily\small\bfseries\color{wpaccent}\MakeUppercase{\wp@classification}}
\fancyfoot[C]{\sffamily\small\color{wpgrey}\thepage}
\renewcommand{\headrulewidth}{0.4pt}

% Title page
\renewcommand{\maketitle}{%
  \begin{titlepage}
    \raggedright
    \includegraphics[height=14mm]{logo.pdf}\par
    \vspace{45mm}
    {\sffamily\bfseries\fontsize{26}{31}\selectfont\color{wpnavy}\@title\par}
    \vspace{5mm}
    {\sffamily\Large\color{wpgrey}\wp@subtitle\par}
    \vspace{3mm}
    {\color{wpaccent}\rule{40mm}{1.2pt}\par}
    \vspace{12mm}
    {\sffamily\large\def\and{\\}\@author\par}
    \vspace{3mm}
    {\sffamily\@date\par}
    \vfill
    {\sffamily\small\color{wpgrey}%
      \begin{tabular}{@{}ll@{}}
        Document & \wp@docid \\
        Version & \wp@version \\
        Classification & \wp@classification \\
      \end{tabular}\par}
  \end{titlepage}
  \setcounter{page}{2}%
}

\renewcommand{\abstractname}{Executive summary}

% Custom environments
\newtcolorbox{keyfinding}{colback=wptint,colframe=wpnavy,coltitle=wpnavy,
  colbacktitle=wptint,boxrule=0pt,leftrule=3pt,arc=0pt,
  fonttitle=\sffamily\bfseries,title=Key finding}
\newtcolorbox{recommendation}[1][Recommendation]{colback=white,colframe=wpaccent,
  coltitle=white,colbacktitle=wpaccent,boxrule=0.6pt,arc=0pt,
  fonttitle=\sffamily\bfseries,title={#1}}
