Feature: Virtual machine lifecycle

  Scenario: A platform consumer can provision and remove a virtual machine
    Given a configured OpenStack test environment
    And a usable image, flavor, and network
    When the consumer runs the virtual machine lifecycle
    Then the lifecycle should succeed

  Scenario: A platform consumer can provision a workload with an address on the requested network
    Given a configured OpenStack test environment
    And a usable image, flavor, and network
    When the consumer provisions a workload on the requested network
    Then the workload should receive an address on that network and be cleaned up
