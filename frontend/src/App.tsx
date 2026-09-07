import { Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import PortfolioBuilder from "./pages/PortfolioBuilder";
import Results from "./pages/Results";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<PortfolioBuilder />} />
        <Route path="results" element={<Results />} />
      </Route>
    </Routes>
  );
}
