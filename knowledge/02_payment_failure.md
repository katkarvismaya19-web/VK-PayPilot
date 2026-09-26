# Payment Failure Recovery Playbook

## Principle
A failed payment is not a lack of intent. The customer already decided to buy. Never offer a discount to recover a failed payment; fix the payment path instead.

## Recovery by failure reason
Failed UPI payments (upi_pin_incorrect or upi_app_declined): send a retry link within 30 minutes and suggest trying a different UPI app. These usually convert on retry.
payment_timeout or bank_server_down: the problem was temporary. Send a retry link after 1 hour with a short note that the bank had a hiccup.
insufficient_funds: do not message immediately. Wait 24 hours, then send a gentle reminder with the option to pay later or split the order.
Failed card payments (bank_declined, authentication_failed or card_expired): suggest switching to UPI or netbanking in the retry message, since the card itself is the obstacle.

## Retry mechanics with Razorpay
Create a fresh Razorpay Payment Link for the failed amount rather than reusing the failed order. Set reminder_enable to true so Razorpay also nudges the customer. Record the new payment link id against the cart so a later successful payment can be attributed to the recovery action.

## Monitoring
If the payment success rate over the last 30 days drops below 90 percent, flag it to the merchant as an operational issue, since it usually points to a gateway configuration or method-specific problem rather than customer behaviour.
