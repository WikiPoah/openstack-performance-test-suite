Feature: Product infrastructure availability

  Scenario: Critical product infrastructure remains active and correctly attached
    Given a configured read-only infrastructure environment
    When the consumer inspects the critical server attachment
    Then the critical server should be active and correctly attached
