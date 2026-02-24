-- Add portfolio value metric to milestones.
-- Total portfolio value = team_value + unspent budget at snapshot time.
ALTER TABLE milestones
    ADD COLUMN IF NOT EXISTS show_portfolio_value BOOLEAN NOT NULL DEFAULT FALSE;