# Action

Alert routing and review-case creation: Activator rules, Power Automate flows and the SharePoint review-case list.

**Owner:** Dashboard and action work package.

## Proposed contents

The blueprint (`site/index.html`, sections 03 and 05) defines the design.

- Activator rule definitions that react to `risk_updated` events.
- Power Automate flow exports for the review-case writer.
- The SharePoint review-case list schema.
- Routing tests and sample audit records.

## Routing order

1. An active payment-support request goes to trained servicing staff first, whatever the churn score (INV-1).
2. A verified renewal-portal software fault goes to technical support.
3. An eligible external-refinancing risk candidate goes to a servicing adviser for a renewal review.

## Rules

- INV-3: at most one open review case exists per `mortgage_id` + `cycle_id` + `action_family`. A duplicate write reads the existing case (T01, T06).
- INV-6: the prototype sends no customer messages. It makes no rate or credit decisions.
- Payment-support priority does not depend on refinancing risk or loan balance.
- Every review case records evidence IDs, routing policy, assigned owner and decision. Score-based cases also record model version, run and snapshot.
- Power Automate licences and connector permissions are not confirmed yet.
- Never put credentials or connection secrets in an exported flow.
