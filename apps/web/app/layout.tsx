import "./style.css";
export const metadata = {
  title: "SpareAgent · Friends beta",
  description: "Voluntary, owner-controlled coding tasks for a private group.",
};
export default function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
