# GCC Games — Historical baseline data

This folder holds prior-edition results used by the `📈 vs 2022` dashboard tab.

| File | Edition | Status |
|---|---|---|
| `gcc_2022_medal_table.csv`     | 3rd GCC Games, Kuwait 2022     | ✅ Populated |
| `gcc_2022_ksa_by_sport.csv`    | 3rd GCC Games, Kuwait 2022     | ✅ Populated |
| `gcc_2017_medal_table.csv`     | 2nd GCC Games, Kuwait 2017 (?) | 🟡 Placeholder — fill if data exists |
| `gcc_2011_medal_table.csv`     | 1st GCC Games, Bahrain 2011    | 🟡 Placeholder — fill if data exists |

## Column convention (medal table)

| Col | Description |
|---|---|
| Rank | 1–6 |
| NOC  | 3-letter (KSA, KUW, BRN, UAE, QAT, OMA) |
| Country | Full name |
| Gold / Silver / Bronze / Total | medal counts |
| Male / Female | medals by gender (optional) |
| Female_Pct | % of total that were female (optional) |
| Host | informational note |

## Column convention (KSA by sport)

| Col | Description |
|---|---|
| Sport | name |
| Gold / Silver / Bronze / Total | KSA medals in that sport |
| In_2026 | yes / no / partial — flag whether that sport (or close equivalent) appears in the GCC 2026 programme |

The dashboard subtracts medals from sports flagged `In_2026 = no` to compute a like-for-like target.
