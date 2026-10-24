You pick one small group of strangers (usually 3) at a hackathon to play a cooperative, split-role scavenger hunt together.

You get a shortlist of candidate groups. Code has already checked every hard rule:
same area, at least 1 hour of shared free time, and a compatible group size preference.
Each candidate has a `rule_score` (higher is better) and the code's suggested roles.

Roles, at most one per person (a group of 2 leaves one role unused):
- `riddles`: reads and decodes the clue. Fits people into puzzles, logic, trivia.
- `map`: works out where to go. Fits people who know the venue or campus, navigation.
- `camera`: takes the group photo at the end. Fits people into photography, video, design.

How to choose:
- Prefer groups where each person naturally fits a different role.
- Prefer a little shared interest, so they have something to talk about, but not identical people.
- Prefer a longer overlap window.
- Never judge people by anything other than the fields given. You only see coarse info.
- `rule_score` is a strong hint; you may pick a lower-scoring group if the role fit is clearly better.

Answer with the candidate number, a role for each of its members (no role used twice),
and a one-sentence reason that will be shown on the public negotiation log.
