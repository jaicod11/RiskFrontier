import { Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import PortfolioBuilder from "./pages/PortfolioBuilder";
import Results from "./pages/Results";
import { HealthProvider } from "./store/health";
import { PortfolioProvider } from "./store/portfolio";

export default function App() {
  return (
    <HealthProvider>
      <PortfolioProvider>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<PortfolioBuilder />} />
            <Route path="results" element={<Results />} />
          </Route>
        </Routes>
      </PortfolioProvider>
    </HealthProvider>
  );
}
