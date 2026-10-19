import "./style.css";
export const metadata = {
  title: "SpareAgent · 好友内测 / Friends beta",
  description: "Voluntary, owner-controlled coding tasks for a private group.",
};
export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
