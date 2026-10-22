// Small file and task icons for the member's task list (no icon font or external assets).

const FILE_KINDS: [RegExp, string, string][] = [
  [/\.pdf$/i, "PDF", "#d93a2b"],
  [/\.(xlsx|xls|csv)$/i, "XLS", "#1f8a4c"],
  [/\.(docx|doc)$/i, "DOC", "#2b6cd4"],
  [/\.(png|jpe?g|gif|webp|svg)$/i, "IMG", "#8a4fd8"],
  [/\.(md|txt|json|html)$/i, "TXT", "#5f6b7a"],
  [/\.(diff|patch|py|js|ts|tsx|go|rs|java|c|cpp|swift|rb|sh)$/i, "</>", "#3c4b5c"],
];

export function FileIcon({ name, size = 28 }: { name?: string | null; size?: number }) {
  const [, label, color] = FILE_KINDS.find(([re]) => re.test(name || "")) || [null, "", "#8a94a3"];
  return (
    <svg width={size} height={size} viewBox="0 0 28 28" aria-hidden="true" className="file-icon">
      <path d="M6 2h11l7 7v15a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2z" fill="#fff" stroke={color} strokeWidth="1.4" />
      <path d="M17 2v5a2 2 0 0 0 2 2h5" fill="none" stroke={color} strokeWidth="1.4" />
      {label && (
        <text x="14" y="21" textAnchor="middle" fontSize="6.5" fontWeight="700" fill={color} fontFamily="-apple-system, sans-serif">
          {label}
        </text>
      )}
    </svg>
  );
}

const KIND_FILES: Record<string, string> = {
  TRANSLATE_DOCUMENT: "x.docx",
  ANALYZE_SPREADSHEET: "x.xlsx",
  GENERAL: "x.md",
};

// A task's icon: the deliverable it produces, or code for repository kinds.
export function TaskIcon({ kind, file }: { kind?: string; file?: string | null }) {
  return <FileIcon name={file || KIND_FILES[kind || ""] || "x.patch"} size={30} />;
}
