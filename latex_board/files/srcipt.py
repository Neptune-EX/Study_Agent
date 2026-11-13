# -*- coding: gbk -*-
import csv
import re

def compute(expr):
    s = expr.replace('\\times', '*').replace('\\div', '/')
    try:
        result = eval(s)
    except:
        return None
    if '/' in s:
        a, b = map(int, s.split('/'))
        if a % b == 0:
            return f"{a // b}"
        else:
            return f"{a // b}\\ldots {a % b}"
    else:
        return str(int(result))

with open('equations.csv', 'r', encoding='utf-8') as f:
    reader = csv.DictReader(f)
    days = list(reader)

lines = []
lines.append(r'\documentclass[10pt,a4paper]{ctexart}')
lines.append(r'\usepackage{geometry}')
lines.append(r'\geometry{a4paper, margin=2cm}')
lines.append(r'\usepackage{fancyhdr}')
lines.append(r'\pagestyle{fancy}')
lines.append(r'\fancyhf{}')
lines.append(r'\chead{\Large 竖式计算练习答案（30天）}')
lines.append(r'\cfoot{\thepage}')
lines.append(r'\renewcommand{\headrulewidth}{0.4pt}')
lines.append(r'\setlength{\parindent}{0pt}')
lines.append(r'\begin{document}')
lines.append(r'\tableofcontents')
lines.append(r'\newpage')

for row in days:
    day = row['Day']
    qs = [row[f'Q{i}'] for i in range(1, 9)]
    ans = [compute(q) for q in qs]
    lines.append(f'\\section*{{第{day}天}}')
    lines.append(r'\begin{enumerate}')
    for q, a in zip(qs, ans):
        lines.append(f'    \\item ${q} = {a}$')
    lines.append(r'\end{enumerate}')
    lines.append(r'\newpage')

lines.append(r'\end{document}')
with open('answers.tex', 'w', encoding='gbk') as f:
    f.write('\n'.join(lines))